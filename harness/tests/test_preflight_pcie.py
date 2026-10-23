#!/usr/bin/env python3
"""No-rental PCIe qualification tests using mocked nvidia-smi and llama-bench."""
import os, subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
PREFLIGHT=ROOT/'harness/scripts/preflight.sh'

NVIDIA=r'''#!/usr/bin/env python3
import os, pathlib, sys
a=' '.join(sys.argv[1:]); marker=pathlib.Path(os.environ['LOAD_MARKER'])
if a=='-q': print('mock nvidia-smi -q')
elif '--query-gpu=name,uuid,memory.total,pci.bus_id,power.limit,power.default_limit' in a: print('RTX 3090, GPU-1, 24576 MiB, 00000000:01:00.0, 350 W, 350 W')
elif '--query-compute-apps=' in a: print('No running processes found')
elif 'pcie.link.gen.current' in a:
    gen=os.environ['LOAD_GEN'] if marker.exists() else '1'
    width=os.environ['LOAD_WIDTH'] if marker.exists() else os.environ.get('IDLE_WIDTH',os.environ['NATIVE_WIDTH'])
    print(f'{gen}, 4, {width}, {os.environ["NATIVE_WIDTH"]}')
elif '--query-gpu=pcie.link.gen.hostmax' in a: print(os.environ.get('HOSTMAX','4'))
else: sys.exit(2)
'''

def run(load_gen, load_width, native_width, idle_width=None):
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); bindir=root/'bin'; bindir.mkdir(); marker=root/'busy'
        def executable(name, content):
            p=bindir/name; p.write_text(content); p.chmod(0o755); return p
        executable('nvidia-smi',NVIDIA)
        executable('lscpu','#!/bin/sh\necho CPU\n')
        executable('vmstat','#!/bin/sh\nprintf "procs memory swap io system cpu\\n r b swpd free buff cache si so bi bo in cs us sy id wa st\\n 0 0 0 0 0 0 0 0 0 0 0 0 0 0 99 0 0\\n"\n')
        executable('curl','#!/bin/sh\nwhile [ "$1" != "-o" ]; do shift; done; shift; : > "$1"\n')
        executable('llama-bench',f'#!/usr/bin/env python3\nimport pathlib,time\np=pathlib.Path({str(marker)!r}); p.touch(); time.sleep(.8); p.unlink(missing_ok=True)\n')
        out=root/'out'; model=root/'tiny.gguf'; model.write_bytes(b'mock'); env=os.environ.copy(); env.update({'PATH':f'{bindir}:{env["PATH"]}','LOAD_MARKER':str(marker),'LOAD_GEN':str(load_gen),'LOAD_WIDTH':str(load_width),'NATIVE_WIDTH':str(native_width),'HOSTMAX':'4','VTM_PCIE_LOAD_SECONDS':'1','VTM_PREFLIGHT_MODEL':str(model),'VTM_LLAMA_BENCH':str(bindir/'llama-bench')})
        if idle_width is not None: env['IDLE_WIDTH']=str(idle_width)
        proc=subprocess.run(['bash',str(PREFLIGHT),'any',str(native_width),'4',str(out)],capture_output=True,text=True,env=env,timeout=10)
        csv=(out/'gpu-preflight.csv').read_text() if (out/'gpu-preflight.csv').exists() else ''
        return proc, csv

def test_idle_downshift_load_gen4_width16_passes():
    p,csv=run(4,16,16)
    assert p.returncode==0,p.stderr+'\n'+p.stdout+'\n'+csv
    assert ',idle,1,4,4,16,16' in csv
    assert ',load,4,4,4,16,16' in csv

def test_underload_gen1_rejected():
    p,_=run(1,16,16)
    assert p.returncode==42 and 'under-load PCIe needs gen >=3' in p.stderr,p.stderr

def test_underload_width8_rejected_for_native16():
    p,_=run(4,8,16)
    assert p.returncode==42 and 'native width x16' in p.stderr,p.stderr

def test_4060_ti_native_width8_passes():
    p,_=run(4,8,8)
    assert p.returncode==0,p.stderr

if __name__=='__main__':
    test_idle_downshift_load_gen4_width16_passes()
    test_underload_gen1_rejected()
    test_underload_width8_rejected_for_native16()
    test_4060_ti_native_width8_passes()
    print('preflight_pcie_under_load: 4 cases passed')
