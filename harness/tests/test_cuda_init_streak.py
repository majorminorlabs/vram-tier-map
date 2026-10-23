#!/usr/bin/env python3
"""No-rental tests for identifying repeated CUDA initialization failures."""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'harness/scripts/cuda_init_streak.py'

def run(state, log, text):
    log.write_text(text)
    p=subprocess.run([sys.executable,str(SCRIPT),str(state),str(log)],capture_output=True,text=True)
    return p

def test_same_error_reaches_two():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); state=root/'streak.json'; log=root/'bench.log'
        first=run(state,log,'ggml_cuda_init: failed to initialize CUDA: forward compatibility was attempted on non supported HW\n')
        second=run(state,log,'ggml_cuda_init: failed to initialize CUDA: forward compatibility was attempted on non supported HW\n')
        assert first.returncode==second.returncode==0
        assert json.loads(first.stdout)['consecutive_count']==1
        assert json.loads(second.stdout)['consecutive_count']==2

def test_different_error_starts_new_streak():
    with tempfile.TemporaryDirectory() as td:
        root=Path(td); state=root/'streak.json'; log=root/'bench.log'
        run(state,log,'failed to initialize CUDA: error A\n')
        second=run(state,log,'failed to initialize CUDA: error B\n')
        assert json.loads(second.stdout)['consecutive_count']==1

if __name__=='__main__':
    test_same_error_reaches_two(); test_different_error_starts_new_streak()
    print('cuda_init_streak: 2 cases passed')
