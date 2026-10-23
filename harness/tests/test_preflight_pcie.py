#!/usr/bin/env python3
"""Mocked preflight tests for host-quality thresholds and PCIe traffic measurement."""
import os
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PREFLIGHT=ROOT/'harness/scripts/preflight.sh'

NVIDIA=r'''#!/usr/bin/env python3
import os, pathlib, sys
a=' '.join(sys.argv[1:]); marker=pathlib.Path(os.environ['LOAD_MARKER'])
width=os.environ['MOCK_WIDTH']; power=os.environ['MOCK_POWER']; default=os.environ['MOCK_DEFAULT']
if a=='-q': print(f"Driver Version : {os.environ.get('MOCK_DRIVER','535.154.05')}\nCUDA Version : {os.environ.get('MOCK_CUDA_MAX','12.2')}")
elif '--query-gpu=name,uuid,memory.total,pci.bus_id,power.limit,power.default_limit' in a: print(f"{os.environ['MOCK_GPU']}, GPU-1, 24576 MiB, 00000000:01:00.0, {power} W, {default} W")
elif '--query-compute-apps=' in a: print('No running processes found')
elif '--query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max' in a:
    gen=os.environ['MOCK_GEN'] if marker.exists() else '1'; print(f'{gen}, 4, {width}, {width}')
elif '--query-gpu=pcie.link.gen.hostmax' in a: print(os.environ.get('MOCK_HOSTGEN','4'))
elif '--query-gpu=pcie.link.width.hostmax' in a: print(os.environ.get('MOCK_HOSTWIDTH',width))
elif '--query-gpu=utilization.gpu,pstate,power.limit,power.default_limit' in a: print(f"{os.environ.get('MOCK_UTIL','25')}, {os.environ.get('MOCK_PSTATE','P0')}, {power}, {default}")
else: sys.exit(2)
'''

def executable(path, content):
    path.write_text(content); path.chmod(0o755); return path

def run(policy='card', gpu='RTX 3090', width=16, gen=4, power=100, default=100, load_ms=100, hostwidth=None, driver='535.154.05', cuda_max='12.2'):
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); bindir=root/'bin'; bindir.mkdir(); marker=root/'busy'
        executable(bindir/'nvidia-smi',NVIDIA)
        executable(bindir/'lscpu','#!/bin/sh\necho CPU\n')
        executable(bindir/'vmstat','#!/bin/sh\nprintf "procs memory swap io system cpu\\n r b swpd free buff cache si so bi bo in cs us sy id wa st\\n 0 0 0 0 0 0 0 0 0 0 0 0 0 0 99 0 0\\n"\n')
        bench=f'''#!/usr/bin/env python3\nimport pathlib,time\np=pathlib.Path({str(marker)!r}); p.touch(); time.sleep(.3); print("llama_model_load: offloaded 32/32 layers to GPU"); print("llama_model_load: load time = {load_ms} ms"); p.unlink(missing_ok=True)\n'''
        executable(bindir/'llama-bench',bench)
        model=root/'tiny.gguf'; model.write_bytes(b'mock transfer bytes')
        out=root/'out'; env=os.environ.copy()
        env.update({'PATH':f'{bindir}:{env["PATH"]}','LOAD_MARKER':str(marker),'MOCK_WIDTH':str(width),'MOCK_GEN':str(gen),'MOCK_POWER':str(power),'MOCK_DEFAULT':str(default),'MOCK_GPU':gpu,'MOCK_DRIVER':driver,'MOCK_CUDA_MAX':cuda_max,'MOCK_UTIL':'25','MOCK_PSTATE':'P0','VTM_PREFLIGHT_MODEL':str(model),'VTM_LLAMA_BENCH':str(bindir/'llama-bench'),'VTM_PREFLIGHT_TRANSFER_BYTES':'1000000000','VTM_TRANSFER_REPS':'1'})
        if hostwidth is not None: env['MOCK_HOSTWIDTH']=str(hostwidth)
        proc=subprocess.run(['bash',str(PREFLIGHT),gpu,str(width),'4',str(out),policy,'64000','32','unused','18','unused'],capture_output=True,text=True,env=env,timeout=10)
        csv=(out/'gpu-preflight.csv').read_text() if (out/'gpu-preflight.csv').exists() else ''
        quality=(out/'host-quality.json').read_text() if (out/'host-quality.json').exists() else ''
        return proc,csv,quality

def test_card_power_at_90_percent_passes_with_h2d():
    p,csv,q=run(power=90,default=100)
    assert p.returncode==0,p.stderr+'\n'+p.stdout+'\n'+csv
    assert 'pcie_h2d_gbps' in q and '10.0' in q
    assert ',transfer,' in csv and 'gpu_util_percent' in csv and 'pstate' in csv
    assert '10.000000' in csv
    import csv as csv_module
    rows=list(csv_module.DictReader(csv.splitlines()))
    assert all(row['gpu_util_percent']=='25' and row['pstate']=='P0' for row in rows if row['phase']=='transfer')

def test_driver_below_cuda_requirement_fails_fast():
    p,_,_=run(driver='535.53.00',cuda_max='12.2')
    assert p.returncode==42 and 'below the pinned image requirement' in p.stderr,p.stderr

def test_card_power_below_90_percent_rejected():
    p,_,_=run(power=89,default=100)
    assert p.returncode==42 and 'below 90%' in p.stderr,p.stderr

def test_card_insufficient_h2d_rejected():
    p,_,_=run(load_ms=500)
    assert p.returncode==42 and 'PCIe H2D check failed' in p.stderr,p.stderr

def test_native_x8_passes_with_width_hostmax_8():
    p,_,q=run(gpu='RTX 4060 Ti',width=8,hostwidth=8)
    assert p.returncode==0,p.stderr+'\n'+p.stdout
    assert '"pcie_width_hostmax": 8' in q

def test_native_width_hostmax_mismatch_rejected():
    p,_,_=run(width=16,hostwidth=8)
    assert p.returncode==42 and 'width.max/hostmax native=16' in p.stderr,p.stderr

def test_smoke_warns_but_ignores_power_ram_and_pcie_quality():
    p,csv,q=run(policy='smoke',width=8,power=50,default=100)
    assert p.returncode==0,p.stderr+'\n'+p.stdout
    assert 'WARN: smoke mode records PCIe capability' in p.stdout
    assert '"power_pass": false' in q and '"pcie_h2d_gbps": null' in q

if __name__=='__main__':
    test_card_power_at_90_percent_passes_with_h2d()
    test_driver_below_cuda_requirement_fails_fast()
    test_card_power_below_90_percent_rejected()
    test_card_insufficient_h2d_rejected()
    test_native_x8_passes_with_width_hostmax_8()
    test_native_width_hostmax_mismatch_rejected()
    test_smoke_warns_but_ignores_power_ram_and_pcie_quality()
    print('preflight_quality_and_pcie_traffic: 7 cases passed')
