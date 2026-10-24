#!/usr/bin/env python3
"""Offline RunPod control-plane lifecycle and SIGKILL watchdog tests."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
BACKEND=ROOT/'harness/scripts/runpod_backend.py'

def main():
 with tempfile.TemporaryDirectory(prefix='vtm-runpod-mock-') as td:
  root=Path(td); state=root/'pods.json'; calls=root/'calls.jsonl'; exe=root/'runpodctl'
  exe.write_text('''#!/usr/bin/env python3
import json,os,sys,pathlib
p=pathlib.Path(os.environ["MOCK_PODS"]); calls=pathlib.Path(os.environ["MOCK_CALLS"])
args=sys.argv[1:]
with calls.open("a") as f:f.write(json.dumps(args)+"\\n")
def read(): return json.loads(p.read_text()) if p.exists() else []
def write(x): p.write_text(json.dumps(x))
if args[:2]==["gpu","list"]: print(json.dumps([{"gpuId":"NVIDIA GeForce RTX 3090","displayName":"RTX 3090","communityPricePerHr":0.22,"securePricePerHr":0.50,"available":True}]))
elif args[:2]==["pod","list"]: print(json.dumps(read()))
elif args[:2]==["pod","create"]:
 name=args[args.index("--name")+1]; pod={"id":"pod-1","name":name,"runtimeStatus":"running"}; write(read()+[pod]); print(json.dumps(pod))
elif args[:2]==["pod","delete"]:
 write([x for x in read() if x["id"]!=args[2]]); print("{}")
elif args[:2]==["pod","get"]: print(json.dumps(read()[0] if read() else {}))
elif args[:2]==["ssh","info"]: print(json.dumps({"ip":"127.0.0.1","port":2222,"privateKey":"must-not-be-printed"}))
else: print("{}")
''')
  exe.chmod(0o755)
  env={**os.environ,'VTM_RUNPODCTL':str(exe),'VTM_PINNED_IMAGE':'ghcr.io/majorminorlabs/vtm-llamacpp@sha256:cbaf8a54939ff5365785066196fbe9ff0f162eaf2022de4db15a8768a72107ed','MOCK_PODS':str(state),'MOCK_CALLS':str(calls)}
  def run(*args,**kw):return subprocess.run([sys.executable,str(BACKEND),*args],env=env,text=True,capture_output=True,check=True,**kw)
  quote=json.loads(run('quote','RTX 3090','0.40','COMMUNITY').stdout)
  assert quote['hourly_rate_usd']==0.22 and quote['pod_type']=='community'
  created=json.loads(run('create','vtm-test','RTX 3090','0.40','150','COMMUNITY').stdout)
  assert created['pod_id']=='pod-1' and created['pod_type']=='community'
  assert json.loads(run('ssh-info','pod-1').stdout)=={'host':'127.0.0.1','port':2222}
  run('delete','pod-1'); assert json.loads(run('zero').stdout) is True
  recorded=[json.loads(line) for line in calls.read_text().splitlines()]
  create_args=next(x for x in recorded if x[:2]==['pod','create'])
  assert '--image' in create_args and env['VTM_PINNED_IMAGE'] in create_args
  assert '--cloud-type' in create_args and create_args[create_args.index('--cloud-type')+1]=='COMMUNITY'
  assert create_args[create_args.index('--volume-mount-path')+1]=='/workspace/storage'
  assert create_args[create_args.index('--container-disk-in-gb')+1]=='50'
  assert any(x[:2]==['pod','delete'] for x in recorded), 'teardown did not call RunPod pod delete'
  # SIGKILL the main process. The independently running watchdog sees the dead owner,
  # deletes the labeled pod, polls until absent, then exits.
  label='vtm-kill'; pid=json.loads(run('create',label,'RTX 3090','0.40','150','COMMUNITY').stdout)['pod_id']
  watchdog=root/'watch-state';watchdog.mkdir();(watchdog/'label.txt').write_text(label+'\n');(watchdog/'pod-id.txt').write_text(pid+'\n')
  owner=subprocess.Popen(['sleep','60']);
  wd=subprocess.Popen([sys.executable,str(BACKEND),'watch',str(watchdog),str(owner.pid),str(int(time.time())+120)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  time.sleep(.25);os.kill(owner.pid,9);owner.wait()
  wd.wait(timeout=20)
  assert json.loads(run('zero').stdout) is True, 'SIGKILL watchdog left a pod running'
  calls_seen=[json.loads(line) for line in calls.read_text().splitlines()]
  assert sum(1 for x in calls_seen if x[:2]==['pod','delete'])>=2
  print('RunPod create, shared-digest config, teardown, and SIGKILL watchdog tests passed')

if __name__=='__main__':main()
