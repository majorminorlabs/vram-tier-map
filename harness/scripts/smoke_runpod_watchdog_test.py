#!/usr/bin/env python3
"""Exercise RunPod smoke SIGKILL recovery; all artifacts remain in private notes."""
import argparse,json,os,signal,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
BACKEND=Path(__file__).with_name('runpod_backend.py')
def cli(*args):
 p=subprocess.run([sys.executable,str(BACKEND),*args],capture_output=True,text=True)
 if p.returncode: return None
 try:return json.loads(p.stdout)
 except Exception:return None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);ap.add_argument('--run-id',required=True);ap.add_argument('--startup-timeout',type=int,default=1800);ap.add_argument('--cleanup-timeout',type=int,default=120);a=ap.parse_args()
 out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
 command=['bash',str(Path(__file__).with_name('runpod_run.sh')),'smoke-kill','RTX 3090',str(out),a.run_id]
 log=out/'kill-test-main.log';started=time.monotonic()
 with log.open('w') as f:
  proc=subprocess.Popen(command,cwd=ROOT,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
  deadline=time.monotonic()+a.startup_timeout;marker=out/'kill-test-running'
  while time.monotonic()<deadline and not marker.exists():
   if proc.poll() is not None:break
   time.sleep(1)
  if not marker.exists():
   if proc.poll() is None:proc.kill()
   proc.wait(timeout=10);raise SystemExit(f'RunPod watchdog smoke failed before kill marker; private log: {log}')
  if proc.poll() is not None:raise SystemExit('RunPod smoke main process exited before SIGKILL')
  killed=time.monotonic();os.kill(proc.pid,signal.SIGKILL)
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
 deadline=killed+a.cleanup_timeout;gone=None;label='vtm-'+a.run_id
 while time.monotonic()<deadline:
  pods=cli('list-label',label)
  zero=cli('zero')
  if pods==[] and zero is True: gone=time.monotonic();break
  time.sleep(3)
 elapsed=round((gone or time.monotonic())-killed,2)
 result={'run_id':a.run_id,'main_killed_with':'SIGKILL','watchdog_cleanup_seconds':elapsed if gone else None,'zero_pods_after':gone is not None,'passed':gone is not None}
 (out/'kill-test-result.json').write_text(json.dumps(result,indent=2)+'\n')
 if gone is None:
  cli('destroy-label',label);raise SystemExit(f'RunPod watchdog did not remove pod within {a.cleanup_timeout}s; rescue teardown attempted')
 print(f'RunPod watchdog passed: SIGKILL cleanup in {elapsed}s; zero active pods')
if __name__=='__main__':main()
