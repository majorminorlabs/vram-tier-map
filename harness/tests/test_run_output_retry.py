#!/usr/bin/env python3
"""Verify failed-host metadata can be retried without allowing result overwrite."""
import subprocess, tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CHECK=ROOT/'harness/scripts/check_run_output.sh'

with tempfile.TemporaryDirectory() as td:
    out=Path(td)
    for name in ('instance-id.txt','label.txt','upload-paths.log'):
        (out/name).write_text('prior host metadata')
    p=subprocess.run(['bash',str(CHECK),str(out)],capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    (out/'results.csv').write_text('card_id\n')
    p=subprocess.run(['bash',str(CHECK),str(out)],capture_output=True,text=True)
    assert p.returncode==2 and 'refusing to overwrite' in p.stderr,p.stderr
print('run_output_retry: 2 cases passed')
