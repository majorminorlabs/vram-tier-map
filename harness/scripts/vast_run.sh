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
"$scripts/destroy_label.sh" "$label" "$private/pre-create-cleanup.log"
"$scripts/cleanup_orphans.sh" "$label" "$private/pre-create-cleanup.log"
failed="$private/failed-host-ids.txt"
skip_offers="$private/create-responses/${run_id}.skip-offers"
touch "$skip_offers"
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
startup_deadline=$(( $(date +%s) + 900 ))
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
offer_gpu=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("gpu_name",""))' "$offer")
disk_gb=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_disk_gb"])' "$offer")
pcie_width=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_native_pcie_width"])' "$offer")
native_gen=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["_vtm_native_pcie_gen"])' "$offer")
host_id=$(python3 -c 'import json,sys; x=json.loads(sys.argv[1]); print(x.get("host_id",x.get("machine_id","")))' "$offer")
hourly=$(python3 -c 'import json,sys; x=json.loads(sys.argv[1]); print(x.get("dph_total",x.get("dph")))' "$offer")
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
status=''
while (( $(date +%s) < startup_deadline )); do
  status=$(vastai show instance "$id" --raw 2>/dev/null | python3 -c 'import json,sys; r=json.load(sys.stdin); r=r[0] if isinstance(r,list) else r; print(r.get("actual_status",r.get("status","")))' 2>/dev/null || true)
  [[ "$status" == running ]] && break
  sleep 10
done
[[ "${status:-}" == running ]] || { echo 'instance startup timed out' >&2; exit 1; }
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
"${ssh[@]}" "bash /workspace/preflight.sh '$offer_gpu' '$pcie_width' '$native_gen' '/workspace/output/hosts/$host_path'" || preflight_rc=$?
if (( preflight_rc != 0 )); then
  if (( preflight_rc != 42 )); then
    echo "ERROR: preflight execution failed with status $preflight_rc; treating as harness/host execution failure" >&2
    exit "$preflight_rc"
  fi
  if [[ "${VTM_REJECTED_COUNT:-0}" -lt 2 ]]; then
    printf '%s\n' "$host" >> "$failed"
    sort -u "$failed" -o "$failed"
    echo "host rejected by preflight; destroying and advancing to next offer ($(( ${VTM_REJECTED_COUNT:-0} + 1 ))/3): machine_id=$host" >&2
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
  echo "ERROR: 3 hosts rejected by preflight; smoke/card attempt aborted" >&2
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
    "${ssh[@]}" 'mkdir -p /workspace/output; curl -fL --retry 3 -o /workspace/tinyllama.gguf https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories15M-q4_0.gguf; /workspace/gpu_bench.sh /workspace/tinyllama.gguf /workspace/output/smoke-bench.json /workspace/output/smoke-bench.log -ngl 999 -p 512 -n 128 -r 1 -o json; kill "$(cat /workspace/output/telemetry.pid)"'
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
  "${ssh[@]}" "PATH=/workspace/bin:\$PATH python3 /workspace/harness/scripts/card_runner.py --card-id '$card_id' --run-id '$run_id' --host-id '$host_path' --mode full"
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
python3 - "$out" "$run_id" "$card_id" "$id" "$label" "$host_id" "$host" "$hourly" "$runtime" "$started_utc" "$end_utc" "$instance_state" "$host_path" <<'PY'
import csv,json,sys,yaml,pathlib,subprocess
out,run_id,card_id,instance_id,label,host_id,machine_id,hourly,runtime,start,end,raw,host_path=sys.argv[1:]
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
with host_csv.open(newline='') as f: pcie=next(csv.reader(f))
pcie_gen=int(pcie[4].strip()); pcie_width=int(pcie[6].strip())
meta={'card_id':card_id,'run_id':run_id,'tier':tier,'llama_cpp_commit':cfg['llama_cpp_commit'],'image_digest':digest,'image':image,'harness_git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'schema_version':'1.1','methodology_version':'v1','hosts_used':[{'host_id':host_id,'machine_id':machine_id,'pcie_gen_actual':pcie_gen,'pcie_width_actual':pcie_width,'pcie_native_gen':next(c['native_pcie_gen'] for c in cards if c['card_id']==card_id),'pcie_native_width':next(c['native_pcie_width'] for c in cards if c['card_id']==card_id),'pcie_gen3_on_gen4_card':pcie_gen==3 and next(c['native_pcie_gen'] for c in cards if c['card_id']==card_id)==4}],'wall_clock':{'start_utc':start,'end_utc':end,'seconds':int(runtime)},'instances':[{'instance_id':instance_id,'label':label,'host_id':host_id,'machine_id':machine_id,'hourly_rate_usd':float(hourly),'actual_cost_usd':round(actual,6),'cost_source':source}],'cost_usd':round(actual,6)}
pathlib.Path(out,'run.json').write_text(json.dumps(meta,indent=2)+'\n')
print('run metadata saved; cost source='+source)
PY
fi
echo "pulled results; instance=$id host=$host hourly=$hourly"
