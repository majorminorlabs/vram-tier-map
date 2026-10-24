#!/usr/bin/env python3
"""Timestamped host exclusions with a 24-hour TTL for transient host faults."""
import json,sys,time
from pathlib import Path
TEMP=('startup stall','startup timed out','ssh timeout','ssh endpoint','ssh/image','image timeout','image readiness','no status change','exceeded 25 minute','terminal startup status','cpu busy','gpu process present')
PERM=('cuda initialization','cuda init','pcie','power cap','disk-full','disk full')

def bucket(reason):
 s=reason.casefold()
 if any(x in s for x in TEMP): return 'temporary'
 if any(x in s for x in PERM): return 'permanent'
 return 'permanent'

def active(path):
 p=Path(path); now=time.time(); out=set()
 if not p.exists(): return out
 for line in p.read_text().splitlines():
  try:
   r=json.loads(line); ttl=86400 if r.get('expiry_class')=='24h' else None
   if ttl is None or now-float(r['timestamp_epoch'])<ttl: out.add(str(r['machine_id']))
  except Exception: continue
 return out

def add(path,host,reason):
 if not host:return
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True); now=time.time(); kind=bucket(reason)
 row={'machine_id':str(host),'reason':reason,'timestamp_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now)),'timestamp_epoch':now,'expiry_class':'24h' if kind=='temporary' else 'permanent'}
 with p.open('a') as f:f.write(json.dumps(row)+'\n')
 if kind=='permanent':
  permanent=p.parent/'failed-host-ids.txt'; existing=set(permanent.read_text().split()) if permanent.exists() else set()
  if str(host) not in existing:
   with permanent.open('a') as f:f.write(str(host)+'\n')
 return row

if __name__=='__main__':
 op=sys.argv[1]
 if op=='active': print('\n'.join(sorted(active(sys.argv[2]))))
 elif op=='add':
  row=add(sys.argv[2],sys.argv[3],sys.argv[4]); print(json.dumps(row or {}))
 else: raise SystemExit('usage: exclusions.py active FILE | add FILE MACHINE REASON')
