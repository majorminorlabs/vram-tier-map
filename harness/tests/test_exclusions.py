#!/usr/bin/env python3
import importlib.util,json,time,tempfile
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'scripts/exclusions.py'
spec=importlib.util.spec_from_file_location('exclusions',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with tempfile.TemporaryDirectory() as td:
 f=Path(td)/'exclusions.jsonl';m.add(f,'host-a','SSH timeout');m.add(f,'host-b','PCIe bandwidth below threshold')
 rows=[json.loads(x) for x in f.read_text().splitlines()]
 assert rows[0]['expiry_class']=='24h' and rows[1]['expiry_class']=='permanent'
 rows[0]['timestamp_epoch']=time.time()-90000;f.write_text('\n'.join(json.dumps(x) for x in rows)+'\n')
 assert m.active(f)=={'host-b'}
 assert (Path(td)/'failed-host-ids.txt').read_text().strip()=='host-b'
print('timestamped exclusion TTL policy passed')
