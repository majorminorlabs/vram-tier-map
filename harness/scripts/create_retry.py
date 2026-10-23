#!/usr/bin/env python3
"""Search, create, diagnose, and retry up to three eligible Vast offers."""
import json, os, subprocess, sys, time
from pathlib import Path
import yaml
from offer_filter import filter_offers

run_id, label, image, cap, gpu, mode, registry_path, private, failed_path = sys.argv[1:]
private = Path(private); diagnostics = private / 'create-responses'; diagnostics.mkdir(parents=True, exist_ok=True)
registry = yaml.safe_load(Path(registry_path).read_text())['cards']
min_disk = min(int(c['disk_gb']) for c in registry) if gpu == 'any' else int(next(c['disk_gb'] for c in registry if c['gpu_name'] == gpu))
min_ram = min(int(c.get('ram_floor_gb',32)) for c in registry) if gpu == 'any' else int(next(c.get('ram_floor_gb',32) for c in registry if c['gpu_name'] == gpu))
skip_path = diagnostics / f'{run_id}.skip-offers'
skip_path.touch(exist_ok=True)
skipped = set(skip_path.read_text().split())
failed = set(Path(failed_path).read_text().split()) if Path(failed_path).exists() else set()
errors = []
ledger = private / f"session-spend-{__import__('time').strftime('%Y%m%d')}.jsonl"
spent = 0.0
if ledger.exists():
    for line in ledger.read_text().splitlines():
        try: spent += float(json.loads(line).get('estimated_cost_usd', 0))
        except Exception: pass
max_hours = 6.0 if mode == 'card' else .5
for attempt in range(1, 4):
    # The CLI query uses the lowest coarse pcie_bw floor for mixed smoke
    # searches; filter_offers applies the native-width-specific floor per card.
    pcie_bw_floor = 6 if gpu == 'any' else (8 if int(next(c['native_pcie_width'] for c in registry if c['gpu_name'] == gpu)) == 16 else 6)
    query = f'external=false num_gpus=1 cpu_ram>={min_ram} reliability>=0.97 rentable=true dph<={cap} pci_gen>=3 pcie_bw>={pcie_bw_floor} disk_space>={min_disk}'
    if gpu != 'any': query += f' gpu_name={gpu.replace(" ", "_")}'
    if failed: query += ' machine_id notin [' + ','.join(sorted(failed)) + ']'
    if skipped: query += ' id notin [' + ','.join(sorted(skipped)) + ']'
    p = subprocess.run(['vastai','search','offers','--raw','--no-default','--limit','100','--order','dph_total',query],capture_output=True,text=True)
    if p.returncode:
        errors.append('offer search failed (Vast CLI exit '+str(p.returncode)+')')
        break
    try: raw = json.loads(p.stdout)
    except Exception:
        errors.append('offer search returned non-JSON output')
        break
    offer = filter_offers(raw, cap, gpu, registry, skipped, failed, mode in ('smoke','smoke-kill'))
    if offer is None:
        errors.append('no eligible offer remains after disk and hardware filters')
        break
    offer_id = str(offer['id']); disk_gb = int(offer['_vtm_disk_gb'])
    projected = float(offer.get('dph_total', offer.get('dph', 1e9))) * 6
    if projected > 20:
        errors.append(f'offer {offer_id} exceeds the card projected-spend limit')
        break
    session_projected = float(offer.get('dph_total', offer.get('dph', 1e9))) * max_hours
    if spent + session_projected > 6.0:
        errors.append(f'offer {offer_id} would exceed the $6 session spend cap (spent estimate ${spent:.4f}, projected additional ${session_projected:.4f})')
        break
    cmd = [sys.executable, str(Path(__file__).with_name('vast_create.py')), run_id, label, offer_id, image, str(disk_gb), str(diagnostics), str(attempt)]
    create = subprocess.run(cmd,capture_output=True,text=True)
    try:
        response=json.loads(create.stdout)
        success=isinstance(response,dict) and response.get('success') is True and response.get('new_contract') not in (None,'')
    except Exception:
        response=None; success=False
    if create.returncode == 0 and success:
        created_epoch=time.time(); instance_id=str(response['new_contract'])
        state=private/'watchdogs'/run_id; state.mkdir(parents=True,exist_ok=True)
        (state/'active-instance-id.txt').write_text(instance_id+'\n')
        (state/'active-instance-meta.json').write_text(json.dumps({'run_id':run_id,'instance_id':instance_id,'hourly':float(offer.get('dph_total',offer.get('dph'))),'started_epoch':created_epoch,'ledger':str(ledger)},indent=2)+'\n')
        result={'offer':offer,'instance_id':instance_id,'create_attempt':attempt,'session_ledger':str(ledger),'max_runtime_hours':max_hours,'created_epoch':created_epoch}
        print(json.dumps(result))
        raise SystemExit(0)
    stderr_path=diagnostics/f'{run_id}-attempt-{attempt:02d}.stderr.txt'
    stderr=stderr_path.read_text(errors='replace') if stderr_path.exists() else ''
    if 'no_such_ask' in stderr:
        errors.append(f'offer {offer_id} create attempt {attempt} returned no_such_ask; immediately re-searching after orphan cleanup; redacted stderr: {stderr.strip() or "<empty>"}')
    else:
        errors.append(f'offer {offer_id} create attempt {attempt} failed; redacted stderr: {stderr.strip() or "<empty>"}')
    # Clean up any instance created despite the failed response before skipping the offer.
    cleanup_log=diagnostics/f'{run_id}-cleanup.log'
    for cleaner in ('destroy_label.sh','cleanup_orphans.sh'):
        result=subprocess.run([str(Path(__file__).with_name(cleaner)),label,str(cleanup_log)],capture_output=True,text=True)
        if result.returncode:
            errors.append(f'offer {offer_id} failed-create cleanup failed in {cleaner}: {result.stderr.strip() or "no diagnostic"}')
            for error in errors: print(error,file=sys.stderr)
            raise SystemExit(1)
    skipped.add(offer_id); skip_path.write_text('\n'.join(sorted(skipped))+'\n')
for error in errors: print(error,file=sys.stderr)
raise SystemExit(1)
