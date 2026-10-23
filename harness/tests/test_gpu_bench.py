#!/usr/bin/env python3
"""Mock the smoke evidence verifier without renting a GPU."""
import os, subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'harness/scripts/gpu_bench.sh'

NVIDIA=r'''#!/usr/bin/env python3
import os,sys,pathlib
a=' '.join(sys.argv[1:]); marker=pathlib.Path(os.environ['MOCK_GPU_BUSY'])
if '--query-gpu=name' in a: print('NVIDIA GeForce RTX 3060')
elif '--query-gpu=memory.used' in a: print('223' if marker.exists() else '18')
else: sys.exit(2)
'''

BENCH=r'''#!/usr/bin/env python3
import json,os,pathlib,sys,time
args=sys.argv[1:]; pathlib.Path(os.environ['MOCK_BENCH_ARGS']).write_text(' '.join(args))
marker=pathlib.Path(os.environ['MOCK_GPU_BUSY']); marker.touch()
print(json.dumps([{'gpu_info':'NVIDIA GeForce RTX 3060','n_gpu_layers':999}]))
if os.environ.get('MOCK_OFFLOAD','yes')=='yes': print('llama_model_load: offloaded 5/5 layers to GPU',file=sys.stderr)
print('ggml_cuda_init: found 1 CUDA devices: NVIDIA GeForce RTX 3060',file=sys.stderr)
time.sleep(.4); marker.unlink(missing_ok=True)
'''

def executable(path,text):
    path.write_text(text); path.chmod(0o755); return path

def test_smoke_gpu_evidence_pass_and_verbose_flag():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); bindir=root/'bin'; bindir.mkdir(); marker=root/'busy'
        executable(bindir/'nvidia-smi',NVIDIA); bench=executable(bindir/'llama-bench',BENCH)
        model=root/'tiny.gguf'; model.write_bytes(b'weights')
        env=os.environ.copy(); env.update({'PATH':f'{bindir}:{env["PATH"]}','LLAMA_BENCH':str(bench),'MOCK_GPU_BUSY':str(marker),'MOCK_BENCH_ARGS':str(root/'args')})
        log=root/'bench.log'; result=root/'bench.json'
        p=subprocess.run(['bash',str(SCRIPT),str(model),str(result),str(log),'-ngl','999','-v'],capture_output=True,text=True,env=env,timeout=10)
        assert p.returncode==0,p.stderr
        assert '-v' in (root/'args').read_text()
        assert 'NVIDIA GeForce RTX 3060' in log.read_text()
        assert 'offloaded 5/5 layers to GPU' in log.read_text()
        assert 'GPU verification passed' in p.stdout, p.stdout

def test_cpu_only_evidence_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); bindir=root/'bin'; bindir.mkdir(); marker=root/'busy'
        executable(bindir/'nvidia-smi',NVIDIA); bench=executable(bindir/'llama-bench',BENCH)
        model=root/'tiny.gguf'; model.write_bytes(b'weights')
        env=os.environ.copy(); env.update({'PATH':f'{bindir}:{env["PATH"]}','LLAMA_BENCH':str(bench),'MOCK_GPU_BUSY':str(marker),'MOCK_BENCH_ARGS':str(root/'args'),'MOCK_OFFLOAD':'no'})
        p=subprocess.run(['bash',str(SCRIPT),str(model),str(root/'bench.json'),str(root/'bench.log'),'-ngl','999','-v'],capture_output=True,text=True,env=env,timeout=10)
        assert p.returncode!=0 and 'no GPU layer offload count' in p.stderr,p.stderr

if __name__=='__main__':
    test_smoke_gpu_evidence_pass_and_verbose_flag(); test_cpu_only_evidence_is_rejected()
    print('gpu_bench_evidence: 2 cases passed')
