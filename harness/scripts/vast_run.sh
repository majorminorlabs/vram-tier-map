#!/usr/bin/env bash
set -euo pipefail
mode=${1:?usage: vast_run.sh smoke|smoke-kill|card GPU_NAME HOURLY_CAP OUTPUT_DIR [RUN_ID]}
gpu=${2:?GPU_NAME}
cap=${3:?HOURLY_CAP}
out=${4:?OUTPUT_DIR}
repo=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo"
scripts="$repo/harness/scripts"
private="$repo/../_private/vram-tier-map"
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then
  cap=0.30
elif [[ "$mode" == card ]]; then
  cap=$(python3 - "$gpu" <<'PY'
import json,sys
cards=json.load(open('harness/registry/cards.yaml'))['cards']
c=next((x for x in cards if x['gpu_name']==sys.argv[1]),None)
if not c: raise SystemExit('GPU is absent from card registry')
print(c['vast_price_cap_usd_per_hour'])
PY
)
fi
mkdir -p "$out" "$private/create-responses" "$private/watchdogs"
if [[ "$mode" == card && -n "$(find "$out" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  echo 'refusing to overwrite an existing committed or partial run directory; use a new run_id' >&2
  exit 2
fi
if [[ -n "${5:-}" ]]; then run_id=$5
elif [[ "$mode" == card ]]; then echo 'card mode requires explicit run_id' >&2; exit 2
else run_id="${mode}-$(date -u +%Y%m%dT%H%M%SZ)"; fi
[[ "$run_id" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'run_id contains unsupported characters' >&2; exit 2; }
ssh_key=''
for candidate in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do
  if [[ -r "$candidate" && -r "$candidate.pub" ]]; then ssh_key=$candidate; break; fi
done
[[ -n "$ssh_key" ]] || { echo 'No readable SSH key pair found at ~/.ssh/id_ed25519 or ~/.ssh/id_rsa' >&2; exit 2; }
label="vtm-$run_id"
state="$private/watchdogs/$run_id"
mkdir -p "$state"
deadline=$(( $(date +%s) + 21600 ))
max_runtime_seconds=21600
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then
  max_runtime_seconds=1800
  deadline=$(( $(date +%s) + max_runtime_seconds ))
fi
host_retry_limit=3
[[ "$mode" != card ]] || host_retry_limit=5
"$scripts/destroy_label.sh" "$label" "$private/pre-create-cleanup.log"
"$scripts/cleanup_orphans.sh" "$label" "$private/pre-create-cleanup.log"
failed="$private/failed-host-ids.txt"
skip_offers="$private/create-responses/${run_id}.skip-offers"
touch "$skip_offers"
host_replication='not_applicable'
eligible_host_count=0
if [[ "$mode" == card ]]; then
  card_id=$(python3 - "$gpu" <<'PY'
import json,sys
cards=json.load(open('harness/registry/cards.yaml'))['cards']
c=next((x for x in cards if x['gpu_name']==sys.argv[1]),None)
if not c: raise SystemExit('GPU is absent from card registry')
print(c['card_id'])
PY
)
  funnel=$(python3 "$scripts/offer_funnel.py" "$gpu" "$cap" "$repo/harness/registry/cards.yaml" "$private" "$failed" "$run_id") || exit $?
  eligible_host_count=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["eligible_host_count"])' "$funnel")
  host_replication=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["host_replication"])' "$funnel")
  eligible_offers=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["eligible_offer_count"])' "$funnel")
  if (( eligible_offers == 0 )); then
    report=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["report_path"])' "$funnel")
    echo "card funnel has zero eligible offers; stopping before rental; report=$report" >&2
    exit 3
  fi
fi
image=$(python3 - harness/registry/runtime.yaml <<'PY'
import sys,yaml
cfg=yaml.safe_load(open(sys.argv[1]))
name=cfg['image'].split(':',1)[0]
digest=cfg['image_digest']
if not isinstance(digest,str) or not digest.startswith('sha256:') or len(digest)!=71:
 raise SystemExit('runtime image digest is not published and pinned')
print(name+'@'+digest)
PY
)
"$scripts/watchdog.sh" "$label" "$$" "$deadline" "$state" >/dev/null 2>&1 & wd=$!
started_epoch=$(date +%s)
started_utc=$(date -u +%FT%TZ)
id=''
pulled=0
cleanup() {
  rc=$?
  trap - EXIT INT TERM
  if (( pulled == 0 )) && [[ -n "$id" && -n "${ssh_host:-}" && -n "${ssh_port:-}" ]]; then
    mkdir -p "$out/emergency-pull"
    scp -i "$ssh_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$ssh_port" -r "root@$ssh_host:/workspace/output/." "$out/emergency-pull/" >/dev/null 2>&1 || echo 'emergency result pull failed before teardown' >&2
  fi
  if [[ -n "$id" ]]; then
    printf '%s\n' "$id" > "$state/active-instance-id.txt"
    if "$scripts/teardown.sh" "$id" "$label" "$state" >> "$state/teardown.log" 2>&1; then
      if [[ -f "$state/active-instance-meta.json" ]]; then
        read -r meta_run meta_id meta_hourly meta_started meta_ledger < <(python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); print(x["run_id"],x["instance_id"],x["hourly"],x["started_epoch"],x["ledger"])' "$state/active-instance-meta.json")
        python3 "$scripts/record_spend.py" "$meta_run" "$meta_id" "$meta_hourly" "$meta_started" "$meta_ledger" >> "$state/spend.log" 2>&1 || rc=1
      fi
    else rc=1; fi
  fi
  touch "$state/main.done"
  wait "$wd" 2>/dev/null || true
  return "$rc"
}
trap cleanup EXIT INT TERM
startup_deadline=$(( $(date +%s) + 1500 ))
provision=$(python3 "$scripts/create_retry.py" "$run_id" "$label" "$image" "$cap" "$gpu" "$mode" "$repo/harness/registry/cards.yaml" "$private" "$failed") || {
  echo "create retry exhausted or provisioning failed; diagnostics: $private/create-responses/${run_id}.stderr.txt" >&2
  exit 1
}
offer=$(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["offer"]))' "$provision")
returned_id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["instance_id"])' "$provision")
create_attempt=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["create_attempt"])' "$provision")
session_ledger=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["session_ledger"])' "$provision")
created_epoch=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["created_epoch"])' "$provision")
offer_id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["id"])' "$offer")
host=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("machine_id",""))' "$offer")
offer_gpu=$(python3 -c 'import json,sys; x=json.loads(sys.argv[1]); print(x.get("_vtm_expected_gpu_name",x.get("gpu_name","")))' "$offer")
disk_gb=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_disk_gb"])' "$offer")
pcie_width=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_native_pcie_width"])' "$offer")
native_gen=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_native_pcie_gen"])' "$offer")
host_id=$(python3 -c 'import json,sys; x=json.loads(sys.argv[1]); print(x.get("host_id",x.get("machine_id","")))' "$offer")
hourly=$(python3 -c 'import json,sys; x=json.loads(sys.argv[1]); print(x.get("dph_total",x.get("dph")))' "$offer")
instance_cpu_ram=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("cpu_ram",""))' "$offer")
verified_status=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("_vtm_verified_status","unknown"))' "$offer")
id="$returned_id"
printf '%s\n' "$id" > "$state/active-instance-id.txt"
python3 - "$state/active-instance-meta.json" "$run_id" "$id" "$hourly" "$session_ledger" "$created_epoch" <<'PY'
import json,sys
path,run_id,instance_id,hourly,ledger,started=sys.argv[1:]
json.dump({'run_id':run_id,'instance_id':instance_id,'hourly':float(hourly),'started_epoch':float(started),'ledger':ledger},open(path,'w'),indent=2)
PY
instances=''
for _ in $(seq 1 12); do
  instances=$(vastai show instances --raw 2>/dev/null) || { sleep 5; continue; }
  python3 -c 'import json,sys; x=json.loads(sys.argv[1]); assert isinstance(x,list) or isinstance(x,dict)' "$instances" >/dev/null 2>&1 || { sleep 5; continue; }
  break
done
if [[ -n "$instances" ]]; then
  matched=$(python3 - "$instances" "$label" "$returned_id" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
a=[str(x.get('id')) for x in r if str(x.get('label',''))==sys.argv[2]]
chosen=sys.argv[3] if sys.argv[3] in a else (a[0] if a else '')
print(chosen)
for x in a:
 if x!=chosen: print('duplicate:'+x)
PY
)
  id=$(sed -n '1p' <<<"$matched")
  while IFS= read -r row; do
    [[ "$row" == duplicate:* ]] || continue
    duplicate=${row#duplicate:}
    "$scripts/destroy_instance.sh" "$duplicate" "$label" "$private/duplicate-cleanup.log"
  done <<<"$matched"
fi
if [[ -z "$id" && -n "$returned_id" ]]; then id=$returned_id; fi
if [[ -z "$id" ]]; then
  echo "create returned no adoptable ID; redacted response: $private/create-responses/${run_id}.json" >&2
  exit 1
fi
printf '%s\n' "$id" > "$state/active-instance-id.txt"
printf '%s\n' "$id" > "$out/instance-id.txt"
printf '%s\n' "$label" > "$out/label.txt"
printf 'run_id=%s\ninstance_id=%s\nlabel=%s\nmachine_id=%s\noffer_id=%s\nhourly_usd=%s\ncreate_attempt=%s\ndisk_gb=%s\n' "$run_id" "$id" "$label" "$host" "$offer_id" "$hourly" "$create_attempt" "$disk_gb" > "$private/${run_id}-instance.txt"
echo "created/adopted run=$run_id instance=$id gpu=$offer_gpu hourly=$hourly"
startup_log="$private/startup/${run_id}-${id}.jsonl"
mkdir -p "$private/startup"
startup_rc=0
startup_reason=$(python3 "$scripts/startup_monitor.py" "$id" "$startup_log") || startup_rc=$?
if (( startup_rc == 42 )); then
  printf 'host_id=%s machine_id=%s reason=startup stall detail=%s\n' "$host_id" "$host" "$startup_reason" >> "$private/failed-host-reasons.tsv"
  if [[ -n "$host" ]]; then printf '%s\n' "$host" >> "$failed"; sort -u "$failed" -o "$failed"; fi
  echo "startup host failure; reason=$startup_reason machine_id=$host; destroying and advancing" >&2
  "$scripts/teardown.sh" "$id" "$label" "$state" >> "$state/teardown.log" 2>&1 || exit 1
  if [[ -f "$state/active-instance-meta.json" ]]; then
    read -r meta_run meta_id meta_hourly meta_started meta_ledger < <(python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); print(x["run_id"],x["instance_id"],x["hourly"],x["started_epoch"],x["ledger"])' "$state/active-instance-meta.json")
    python3 "$scripts/record_spend.py" "$meta_run" "$meta_id" "$meta_hourly" "$meta_started" "$meta_ledger" >> "$state/spend.log" 2>&1
    rm -f "$state/active-instance-meta.json"
  fi
  rejected=${VTM_REJECTED_COUNT:-0}
  if (( rejected + 1 < host_retry_limit )); then
    id=''
    touch "$state/main.done"
    wait "$wd" 2>/dev/null || true
    rm -f "$state/main.done" "$state/active-instance-id.txt"
    exec env VTM_REJECTED_COUNT="$((rejected + 1))" "$0" "$mode" "$gpu" "$cap" "$out" "$run_id"
  fi
  echo "ERROR: $host_retry_limit hosts failed startup/preflight; attempt aborted" >&2
  exit 1
elif (( startup_rc != 0 )); then
  echo "startup monitor failed (exit $startup_rc): $startup_reason" >&2
  exit "$startup_rc"
fi
echo "instance startup ready; status log=$startup_log"
startup_deadline=$(( $(date +%s) + 900 ))
if ! vastai attach ssh "$id" "$ssh_key.pub" >/dev/null 2>&1; then
  echo 'failed to attach the selected local SSH public key to the instance' >&2
  exit 1
fi
ssh_host=''; ssh_port=''
while (( $(date +%s) < startup_deadline )); do
  endpoint=$(vastai ssh-url "$id" 2>/dev/null || true)
  read -r ssh_host ssh_port < <(python3 - "$endpoint" <<'PY'
import re,sys
m=re.search(r'(?:ssh://)?(?:\w+@)?([^\s:/]+):(\d+)',sys.argv[1])
if m: print(m.group(1),m.group(2))
else:
 m=re.search(r'-p\s+(\d+)\s+\w+@([^\s]+)',sys.argv[1])
 if not m: raise SystemExit(1)
 print(m.group(2),m.group(1))
PY
  )
  [[ -n "$ssh_host" && -n "$ssh_port" ]] && break
  sleep 10
done
[[ -n "$ssh_host" && -n "$ssh_port" ]] || { echo 'SSH endpoint startup timed out' >&2; exit 1; }
ssh=(ssh -i "$ssh_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$ssh_port" "root@$ssh_host")
scp_opts=(-i "$ssh_key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$ssh_port")
ready=0
while (( $(date +%s) < startup_deadline )); do
  if "${ssh[@]}" 'test -x /workspace/bin/llama-bench' >/dev/null 2>&1; then ready=1; break; fi
  sleep 10
done
(( ready )) || { echo 'pinned llama.cpp image startup timed out' >&2; exit 1; }
upload_log="$out/upload-paths.log"
mkdir -p "$out"
local_sha256() { python3 - "$1" <<'PY'
import hashlib,sys
h=hashlib.sha256()
with open(sys.argv[1],'rb') as f:
 for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
print(h.hexdigest())
PY
}
verify_remote_file() {
  local local_path=$1 remote_path=$2 must_exec=$3 expected remote_cmd
  expected=$(local_sha256 "$local_path")
  [[ "$remote_path" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "unsafe remote upload path: $remote_path" >&2; return 1; }
  remote_cmd="test -f $remote_path"
  [[ "$must_exec" != true ]] || remote_cmd+=" && test -x $remote_path"
  remote_cmd+=" && actual=\$(sha256sum $remote_path | awk '{print \$1}') && test \"\$actual\" = $expected"
  "${ssh[@]}" "$remote_cmd" || { echo "remote upload verification failed: $remote_path" >&2; return 1; }
  printf 'verified remote_path=%s executable=%s sha256=%s\n' "$remote_path" "$must_exec" "$expected" >> "$upload_log"
}
upload_file() {
  local local_path=$1 remote_path=$2 must_exec=${3:-true}
  printf 'upload local_path=%s remote_path=%s\n' "$local_path" "$remote_path" >> "$upload_log"
  scp "${scp_opts[@]}" "$local_path" "root@$ssh_host:$remote_path"
  if [[ "$must_exec" == true ]]; then "${ssh[@]}" "chmod +x $remote_path"; fi
  verify_remote_file "$local_path" "$remote_path" "$must_exec"
}
upload_tree() {
  local local_dir=$1 remote_dir=$2 indexed file relative remote_path expected must_exec remote_cmd remote_parent
  printf 'upload-tree local_path=%s remote_path=%s/\n' "$local_dir" "$remote_dir" >> "$upload_log"
  while IFS= read -r -d '' indexed; do
    relative=${indexed#harness/}
    file="$local_dir/$relative"
    remote_path="$remote_dir/$relative"
    remote_parent=${remote_path%/*}
    expected=$(local_sha256 "$file")
    must_exec=false
    [[ ! -x "$file" ]] || must_exec=true
    [[ "$remote_path" =~ ^/[A-Za-z0-9_./-]+$ ]] || { echo "unsafe remote upload path: $remote_path" >&2; return 1; }
    "${ssh[@]}" "mkdir -p $remote_parent"
    scp "${scp_opts[@]}" "$file" "root@$ssh_host:$remote_path"
    if [[ "$must_exec" == true ]]; then "${ssh[@]}" "chmod +x $remote_path"; fi
    remote_cmd="test -f $remote_path"
    [[ "$must_exec" != true ]] || remote_cmd+=" && test -x $remote_path"
    remote_cmd+=" && actual=\$(sha256sum $remote_path | awk '{print \$1}') && test \"\$actual\" = $expected"
    "${ssh[@]}" "$remote_cmd" || { echo "remote upload verification failed: $remote_path" >&2; return 1; }
    printf 'verified remote_path=%s executable=%s sha256=%s\n' "$remote_path" "$must_exec" "$expected" >> "$upload_log"
  done < <(git -C "$repo" ls-files -z -- harness/)
}
host_path=${host_id//[^A-Za-z0-9_-]/_}
upload_file "$scripts/onstart.sh" /workspace/onstart.sh true
"${ssh[@]}" 'bash /workspace/onstart.sh'
upload_file "$scripts/preflight.sh" /workspace/preflight.sh true
preflight_rc=0
preflight_policy=card
[[ "$mode" == smoke || "$mode" == smoke-kill ]] && preflight_policy=smoke
ram_floor=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_ram_floor_gb"])' "$offer")
transfer_model=$(python3 -c 'import yaml; x=yaml.safe_load(open("harness/registry/models.yaml"))["preflight_transfer"]; print("\t".join([x["url"],str(x["size_bytes"]),x["sha256"]]))')
IFS=$'\t' read -r transfer_url transfer_size transfer_sha <<< "$transfer_model"
"${ssh[@]}" "bash /workspace/preflight.sh '$offer_gpu' '$pcie_width' '$native_gen' '/workspace/output/hosts/$host_path' '$preflight_policy' '$instance_cpu_ram' '$ram_floor' '$transfer_url' '$transfer_size' '$transfer_sha'" || preflight_rc=$?
if (( preflight_rc != 0 )); then
  if (( preflight_rc != 42 )); then
    echo "ERROR: preflight execution failed with status $preflight_rc; treating as harness/host execution failure" >&2
    exit "$preflight_rc"
  fi
  if [[ "$mode" == card ]]; then
    mkdir -p "$private/rejected-hosts/$run_id/$host_path"
    "${ssh[@]}" "tar -C '/workspace/output/hosts/$host_path' -cf - ." 2>/dev/null | tar -C "$private/rejected-hosts/$run_id/$host_path" -xf - || true
  fi
  if (( ${VTM_REJECTED_COUNT:-0} + 1 < host_retry_limit )); then
    printf '%s\n' "$host" >> "$failed"
    sort -u "$failed" -o "$failed"
    printf 'host_id=%s machine_id=%s reason=preflight rejection\n' "$host_id" "$host" >> "$private/failed-host-reasons.tsv"
    echo "host rejected by preflight; destroying and advancing to next offer ($(( ${VTM_REJECTED_COUNT:-0} + 1 ))/$host_retry_limit): machine_id=$host" >&2
    "$scripts/teardown.sh" "$id" "$label" "$state" >> "$state/teardown.log" 2>&1
    if [[ -f "$state/active-instance-meta.json" ]]; then
      read -r meta_run meta_id meta_hourly meta_started meta_ledger < <(python3 -c 'import json,sys; x=json.load(open(sys.argv[1])); print(x["run_id"],x["instance_id"],x["hourly"],x["started_epoch"],x["ledger"])' "$state/active-instance-meta.json")
      python3 "$scripts/record_spend.py" "$meta_run" "$meta_id" "$meta_hourly" "$meta_started" "$meta_ledger" >> "$state/spend.log" 2>&1
      rm -f "$state/active-instance-meta.json"
    fi
    id=''
    touch "$state/main.done"
    wait "$wd" 2>/dev/null || true
    rm -f "$state/main.done" "$state/active-instance-id.txt"
    exec env VTM_REJECTED_COUNT="$(( ${VTM_REJECTED_COUNT:-0} + 1 ))" "$0" "$mode" "$gpu" "$cap" "$out" "$run_id"
  fi
  printf '%s\n' "$host" >> "$failed"
  echo "ERROR: $host_retry_limit hosts rejected by preflight; attempt aborted" >&2
  exit 1
fi
upload_file "$scripts/telemetry.sh" /workspace/telemetry.sh true
"${ssh[@]}" "nohup bash /workspace/telemetry.sh '/workspace/output/hosts/$host_path/telemetry.csv' >'/workspace/output/hosts/$host_path/telemetry.log' 2>&1 </dev/null & echo \$! > '/workspace/output/hosts/$host_path/telemetry.pid'"
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then
  upload_file "$scripts/gpu_bench.sh" /workspace/gpu_bench.sh true
  if [[ "$mode" == smoke-kill ]]; then
    "${ssh[@]}" 'mkdir -p /workspace/output; sleep 300; curl -fL --retry 3 -o /workspace/tinyllama.gguf https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories15M-q4_0.gguf; /workspace/gpu_bench.sh /workspace/tinyllama.gguf /workspace/output/smoke-bench.json /workspace/output/smoke-bench.log -ngl 999 -p 512 -n 128 -r 1 -o json' &
    smoke_pid=$!
    touch "$out/kill-test-running"
    wait "$smoke_pid"
  else
    "${ssh[@]}" "set -e; mkdir -p /workspace/output; curl -fL --retry 3 -o /workspace/tinyllama.gguf https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories15M-q4_0.gguf; /workspace/gpu_bench.sh /workspace/tinyllama.gguf /workspace/output/smoke-bench.json /workspace/output/smoke-bench.log -ngl 999 -p 512 -n 128 -r 1 -o json; kill \"\$(cat '/workspace/output/hosts/$host_path/telemetry.pid')\""
  fi
else
  card_id=$(python3 - "$gpu" <<'PY'
import json,sys
cards=json.load(open('harness/registry/cards.yaml'))['cards']
card=next((c['card_id'] for c in cards if c['gpu_name']==sys.argv[1]),None)
if not card: raise SystemExit('GPU is absent from card registry')
print(card)
PY
)
  upload_tree "$repo/harness" /workspace/harness
  "${ssh[@]}" "PATH=/workspace/bin:\$PATH LD_LIBRARY_PATH=/workspace/llama.cpp/build/bin python3 /workspace/harness/scripts/card_runner.py --card-id '$card_id' --run-id '$run_id' --host-id '$host_path' --instance-cpu-ram '$instance_cpu_ram' --verified-status '$verified_status' --mode full"
  "${ssh[@]}" "if test -f '/workspace/output/hosts/$host_path/telemetry.pid'; then kill \$(cat '/workspace/output/hosts/$host_path/telemetry.pid') 2>/dev/null || true; fi"
fi
copy_dest="$out"
if [[ "$mode" != card ]]; then copy_dest="$out/results"; fi
mkdir -p "$copy_dest"
scp "${scp_opts[@]}" -r "root@$ssh_host:/workspace/output/." "$copy_dest/" || { echo 'result pull failed' >&2; exit 1; }
pulled=1
if [[ "$mode" == card ]]; then
  end_epoch=$(date +%s); end_utc=$(date -u +%FT%TZ); runtime=$((end_epoch-started_epoch))
  instance_state=$(vastai show instance "$id" --raw 2>/dev/null || true)
python3 - "$out" "$run_id" "$card_id" "$id" "$label" "$host_id" "$host" "$hourly" "$runtime" "$started_utc" "$end_utc" "$instance_state" "$host_path" "$instance_cpu_ram" "$verified_status" "$host_replication" "$eligible_host_count" <<'PY'
import csv,json,sys,yaml,pathlib,subprocess
out,run_id,card_id,instance_id,label,host_id,machine_id,hourly,runtime,start,end,raw,host_path,instance_cpu_ram,verified_status,host_replication,eligible_host_count=sys.argv[1:]
cfg=yaml.safe_load(open('harness/registry/runtime.yaml'))
digest=cfg['image_digest']; image=cfg['image'].split(':',1)[0]+'@'+digest
try:
 x=json.loads(raw); x=x[0] if isinstance(x,list) else x
except Exception: x={}
actual=None; source=None
for key in ('actual_cost','total_cost','cost_usd','billed_amount','dollars_spent'):
 try:
  if x.get(key) is not None: actual=float(x[key]); source='vast_instance_record'; break
 except Exception: pass
if actual is None: actual=float(hourly)*int(runtime)/3600; source='hourly_rate_times_runtime_estimate'
cards=json.load(open('harness/registry/cards.yaml'))['cards']; tier=next(c['tier'] for c in cards if c['card_id']==card_id)
host_csv=pathlib.Path(out,'hosts',host_path,'gpu-preflight.csv')
with host_csv.open(newline='') as f: pcie=list(csv.DictReader(f))
def iv(value):
 try: return int(value)
 except (TypeError,ValueError): return None
idle=next(r for r in pcie if r['phase']=='idle'); loads=[r for r in pcie if r['phase']=='transfer']
native_card=next(c for c in cards if c['card_id']==card_id)
under=loads[0] if loads else {}
pcie_gen=max((iv(r.get('pcie_gen_current')) for r in loads if iv(r.get('pcie_gen_current')) is not None),default=None); pcie_width=iv(under.get('pcie_width_current'))
quality_path=pathlib.Path(out,'hosts',host_path,'host-quality.json'); quality=json.loads(quality_path.read_text())
transfer_path=pathlib.Path(out,'hosts',host_path,'pcie-transfer.json'); transfer=json.loads(transfer_path.read_text()) if transfer_path.exists() else {}
evidence_path=pathlib.Path(out,'first-config-cuda-evidence.json'); cuda_evidence=json.loads(evidence_path.read_text()) if evidence_path.exists() else None
pcie_context={'pcie_gen_actual':pcie_gen,'pcie_width_actual':pcie_width,'pcie_gen_idle':iv(idle['pcie_gen_current']),'pcie_gen_max':max((iv(r['pcie_gen_max']) for r in pcie if iv(r['pcie_gen_max']) is not None),default=None),'pcie_gen_hostmax':max((iv(r['pcie_gen_hostmax']) for r in pcie if iv(r['pcie_gen_hostmax']) is not None),default=None),'pcie_width_idle':iv(idle['pcie_width_current']),'pcie_width_max':max((iv(r['pcie_width_max']) for r in pcie if iv(r['pcie_width_max']) is not None),default=None),'pcie_width_hostmax':max((iv(r['pcie_width_hostmax']) for r in pcie if iv(r['pcie_width_hostmax']) is not None),default=None),'power_limit_w':quality.get('power_limit_w'),'power_default_limit_w':quality.get('power_default_limit_w'),'pcie_h2d_gbps':transfer.get('measured_h2d_gbps')}
meta={'card_id':card_id,'run_id':run_id,'tier':tier,'llama_cpp_commit':cfg['llama_cpp_commit'],'image_digest':digest,'image':image,'harness_git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'schema_version':'1.4','methodology_version':'v1.2','first_config_cuda_evidence':cuda_evidence,'host_replication':{'status':host_replication,'eligible_host_count':int(eligible_host_count)},'hosts_used':[{'host_id':host_id,'machine_id':machine_id,'instance_cpu_ram':instance_cpu_ram,'verified_status':verified_status,**pcie_context,'pcie_native_gen':native_card['native_pcie_gen'],'pcie_native_width':native_card['native_pcie_width'],'pcie_gen3_on_gen4_card':pcie_gen==3 and native_card['native_pcie_gen']==4}],'wall_clock':{'start_utc':start,'end_utc':end,'seconds':int(runtime)},'instances':[{'instance_id':instance_id,'label':label,'host_id':host_id,'machine_id':machine_id,'hourly_rate_usd':float(hourly),'actual_cost_usd':round(actual,6),'cost_source':source}],'cost_usd':round(actual,6)}
pathlib.Path(out,'run.json').write_text(json.dumps(meta,indent=2)+'\n')
print('run metadata saved; cost source='+source)
PY
fi
echo "pulled results; instance=$id host=$host hourly=$hourly"
