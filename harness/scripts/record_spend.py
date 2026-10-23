#!/usr/bin/env python3
"""Record one instance's estimated Vast spend once in the private session ledger."""
import json,sys,time
from pathlib import Path
run_id,instance_id,hourly,started,ledger=sys.argv[1:]
p=Path(ledger); p.parent.mkdir(parents=True,exist_ok=True)
rows=[]
if p.exists():
    for line in p.read_text().splitlines():
        try: rows.append(json.loads(line))
        except Exception: pass
if any(r.get('instance_id')==instance_id for r in rows): raise SystemExit(0)
end=time.time(); seconds=max(0,int(end-float(started))); cost=float(hourly)*seconds/3600
with p.open('a') as f: f.write(json.dumps({'run_id':run_id,'instance_id':instance_id,'hourly_rate_usd':float(hourly),'seconds':seconds,'estimated_cost_usd':round(cost,6),'ended_epoch':int(end)})+'\n')
print(f"session spend recorded: instance={instance_id} estimated=${cost:.4f}")
