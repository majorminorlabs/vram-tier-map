#!/usr/bin/env python3
"""Run and privately record one card's offer funnel without creating a rental."""
import json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import yaml
from offer_filter import filter_offers
from cuda_compat import offer_supports_cuda

gpu, cap, registry_path, private, failed_path, run_id = sys.argv[1:]
runtime=yaml.safe_load((Path(registry_path).parents[1]/'registry/runtime.yaml').read_text())
cards=yaml.safe_load(Path(registry_path).read_text())['cards']
card=next((c for c in cards if c['gpu_name']==gpu),None)
if card is None: raise SystemExit('GPU is absent from cards.yaml')
query=f"external=false gpu_name={gpu.replace(' ','_')}"
p=subprocess.run(['vastai','search','offers','--raw','--no-default','--limit','1000','--order','dph_total',query],capture_output=True,text=True)
if p.returncode:
    raise SystemExit(f'offer funnel search failed (Vast CLI exit {p.returncode})')
try:
    raw=json.loads(p.stdout); offers=raw if isinstance(raw,list) else raw.get('offers',[])
except Exception: raise SystemExit('offer funnel search returned non-JSON output')
stages=[]; rows=offers
stages.append(('gpu_name',len(rows)))
rows=[o for o in rows if int(o.get('num_gpus',0) or 0)==1]; stages.append(('num_gpus=1',len(rows)))
rows=[o for o in rows if o.get('rentable') is True]; stages.append(('rentable',len(rows)))
ram_floor=int(card.get('ram_floor_gb',32))
rows=[o for o in rows if float(o.get('cpu_ram',0) or 0)>=ram_floor*1000]; stages.append((f'cpu_ram>={ram_floor}GB',len(rows)))
rows=[o for o in rows if float(o.get('reliability',0) or 0)>=.97]; stages.append(('reliability>=0.97',len(rows)))
verified=sum(1 for o in rows if o.get('verified') is True or o.get('verification')=='verified' or o.get('vericode')==1)
stages.append((f'verified (not required; {verified} verified)',len(rows)))
disk=int(card['disk_gb'])
rows=[o for o in rows if float(o.get('disk_space',0) or 0)>=disk]; stages.append((f'disk_space>={disk}GB',len(rows)))
floor=8 if int(card['native_pcie_width'])==16 else 6
rows=[o for o in rows if float(o.get('pcie_bw',0) or 0)>=floor and float(o.get('pci_gen',0) or 0)>=3]; stages.append((f'pci_gen>=3 and pcie_bw>={floor}',len(rows)))
rows=[o for o in rows if offer_supports_cuda(o,runtime['cuda_max_good_required'],runtime['minimum_driver_version'])]; stages.append((f'cuda_max_good>={runtime["cuda_max_good_required"]} or driver_version>={runtime["minimum_driver_version"]}',len(rows)))
cap=float(cap)
rows=[o for o in rows if float(o.get('dph_total',o.get('dph',1e9)) or 1e9)<=cap]; stages.append((f'price<={cap:.2f}/h',len(rows)))
failed=set(Path(failed_path).read_text().split()) if Path(failed_path).exists() else set()
skip_path=Path(private)/'create-responses'/f'{run_id}.skip-offers'
skipped=set(skip_path.read_text().split()) if skip_path.exists() else set()
eligible=[o for o in rows if str(o.get('machine_id','')) not in failed and str(o.get('id','')) not in skipped]
hosts=sorted({str(o.get('machine_id','')) for o in eligible if o.get('machine_id') is not None})
replication='available' if len(hosts)>1 else 'unavailable'
report=['# Offer funnel (no rental)','',f'Snapshot UTC: {datetime.now(timezone.utc).isoformat()}',f'Card: {card["card_id"]} ({gpu}); hourly cap ${cap:.2f}; RAM floor {ram_floor} GB; verified status is informational.','', '| Filter (cumulative) | Offers |','|---|---:|']
report += [f'| {name} | {count} |' for name,count in stages]
report += ['',f'Eligible offers after existing host/offer exclusions: {len(eligible)}',f'Distinct eligible host IDs: {len(hosts)}',f'Host replication: {replication}']
report += ['', '| Offer ID | Host ID | $/h | pcie_bw | CUDA max | Driver | CPU RAM | Verified status |','|---:|---:|---:|---:|---:|---:|---:|:---:|']
for o in sorted(eligible,key=lambda x:float(x.get('dph_total',x.get('dph',1e9)) or 1e9))[:10]:
    verified=o.get('verified',o.get('verification',o.get('vericode','unknown')))
    report.append(f'| {o.get("id")} | {o.get("machine_id")} | {o.get("dph_total",o.get("dph",""))} | {o.get("pcie_bw","")} | {o.get("cuda_max_good","")} | {o.get("driver_version","")} | {o.get("cpu_ram","")} | {verified} |')
if not eligible: report.append('| — | — | — | — | — | — | — | — |')
out=Path(private)/'reports'/f'offer-funnel-{card["card_id"]}-{run_id}.md'; out.parent.mkdir(parents=True,exist_ok=True); out.write_text('\n'.join(report)+'\n')
print(json.dumps({'card_id':card['card_id'],'eligible_offer_count':len(eligible),'eligible_host_count':len(hosts),'host_replication':replication,'report_path':str(out)}))
