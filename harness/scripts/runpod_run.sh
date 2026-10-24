#!/usr/bin/env bash
set -euo pipefail
mode=${1:?usage: runpod_run.sh smoke|smoke-kill|card GPU_NAME OUTPUT_DIR RUN_ID}
gpu=${2:?GPU_NAME}
out=${3:?OUTPUT_DIR}
run_id=${4:-}
repo=$(cd "$(dirname "$0")/../.." && pwd); cd "$repo"
scripts="$repo/harness/scripts"; private="$repo/../_private/vram-tier-map"
[[ -n "$run_id" ]] || run_id="${mode}-$(date -u +%Y%m%dT%H%M%SZ)"
[[ "$run_id" =~ ^[A-Za-z0-9_-]+$ ]] || { echo 'invalid run_id' >&2; exit 2; }
mkdir -p "$out" "$private/runpod/watchdogs" "$private/runpod/create-responses"
card=$(python3 - "$gpu" <<'PY'
import json,sys
cards=json.load(open('harness/registry/cards.yaml'))['cards']
c=next((x for x in cards if x['gpu_name']==sys.argv[1]),None)
if not c: raise SystemExit('GPU is absent from card registry')
if c.get('provider')!='runpod': raise SystemExit('card provider is not RunPod; refusing to call another provider')
print(json.dumps(c))
PY
)
card_id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["card_id"])' "$card")
disk_gb=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["disk_gb"])' "$card")
cap=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["vast_price_cap_usd_per_hour"])' "$card")
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then cap=0.40; fi
image=$(python3 - <<'PY'
import yaml
x=yaml.safe_load(open('harness/registry/runtime.yaml'))
print(x['image'].split(':',1)[0]+'@'+x['image_digest'])
PY
)
export VTM_PINNED_IMAGE="$image"
label="vtm-$run_id"; state="$private/runpod/watchdogs/$run_id"; mkdir -p "$state"
ledger="$private/runpod/session-spend-$(date -u +%Y%m%d).jsonl"
max_seconds=21600; [[ "$mode" == card ]] || max_seconds=1800
started=$(date +%s); deadline=$((started+max_seconds)); start_utc=$(date -u +%FT%TZ)
key=''; for candidate in "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_rsa"; do [[ -r "$candidate" && -r "$candidate.pub" ]] && { key=$candidate; break; }; done
[[ -n "$key" ]] || { echo 'No SSH keypair available for RunPod SSH' >&2; exit 2; }
# Register the existing local public key only when the account does not already have it.
python3 - "$key.pub" <<'PY'
import json,subprocess,sys,tempfile
pub=open(sys.argv[1]).read().strip()
p=subprocess.run(['runpodctl','ssh','list-keys','--output','json'],capture_output=True,text=True)
if p.returncode: raise SystemExit('RunPod SSH-key listing failed')
try: data=json.loads(p.stdout)
except Exception: raise SystemExit('RunPod SSH-key listing returned malformed JSON')
rows=data if isinstance(data,list) else data.get('sshKeys',data.get('keys',[])) if isinstance(data,dict) else []
keys=[str(x) if isinstance(x,str) else str(x.get('key') or x.get('publicKey') or x.get('sshKey') or '').strip() for x in rows]
if pub not in keys:
 q=subprocess.run(['runpodctl','ssh','add-key','--key-file',sys.argv[1]],capture_output=True,text=True)
 if q.returncode: raise SystemExit('RunPod SSH key registration failed')
PY
python3 "$scripts/runpod_backend.py" destroy-label "$label" >/dev/null
python3 "$scripts/runpod_backend.py" zero | grep -qx true || { echo 'RunPod pre-run zero-pod check failed' >&2; exit 1; }
quote=$(python3 "$scripts/runpod_backend.py" quote "$gpu" "$cap" COMMUNITY) || exit 1
hourly=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["hourly_rate_usd"])' "$quote")
pod_type=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pod_type"])' "$quote")
projected=$(python3 - "$hourly" "$max_seconds" <<'PYQ'
import sys
print(float(sys.argv[1])*int(sys.argv[2])/3600)
PYQ
)
spent=$(python3 - "$ledger" <<'PYS'
import json,sys,pathlib
p=pathlib.Path(sys.argv[1]); total=0
if p.exists():
 for s in p.read_text().splitlines():
  try: total+=float(json.loads(s).get('estimated_cost_usd',0))
  except Exception: pass
print(total)
PYS
)
python3 - "$spent" "$projected" <<'PYG'
import sys
if float(sys.argv[1])+float(sys.argv[2])>6.0: raise SystemExit('RunPod projected spend exceeds $6 session cap')
PYG
printf '%s\n' "$label" > "$state/label.txt"
python3 "$scripts/runpod_backend.py" watch "$state" "$$" "$deadline" >/dev/null 2>&1 & wd=$!
pod_id=''; pulled=0; ssh_host=''; ssh_port=''
cleanup() {
 rc=$?; trap - EXIT INT TERM
 if [[ -n "$pod_id" ]]; then
   if (( pulled == 0 )) && [[ -n "$ssh_host" && -n "$ssh_port" ]]; then
     mkdir -p "$out/emergency-pull"; scp -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$ssh_port" -r "root@$ssh_host:/workspace/output/." "$out/emergency-pull/" >/dev/null 2>&1 || true
   fi
   python3 "$scripts/runpod_backend.py" delete "$pod_id" >> "$state/teardown.log" 2>&1 || rc=1
 else
   python3 "$scripts/runpod_backend.py" destroy-label "$label" >> "$state/teardown.log" 2>&1 || rc=1
 fi
 if [[ -n "$pod_id" ]]; then
  end=$(date +%s); seconds=$((end-started)); cost=$(python3 - "$hourly" "$seconds" <<'PYC'
import sys
print(round(float(sys.argv[1])*int(sys.argv[2])/3600,6))
PYC
)
  if [[ -f "$out/run.json" ]]; then python3 - "$out/run.json" "$seconds" "$cost" <<'PYMETA'
import json,sys,datetime,pathlib
p=pathlib.Path(sys.argv[1]);x=json.loads(p.read_text());now=datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
x['wall_clock']['end_utc']=now;x['wall_clock']['seconds']=int(sys.argv[2]);x['cost_usd']=float(sys.argv[3])
for pod in x.get('instances',[]):pod['actual_cost_usd']=float(sys.argv[3])
p.write_text(json.dumps(x,indent=2)+'\n')
PYMETA
  fi
  python3 - "$ledger" "$run_id" "$pod_id" "$hourly" "$seconds" "$cost" <<'PYL'
import json,sys,pathlib,time
p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True)
rows=[]
if p.exists():
 for line in p.read_text().splitlines():
  try: rows.append(json.loads(line))
  except Exception: pass
if not any(r.get('pod_id')==sys.argv[3] for r in rows):
 with p.open('a') as f:f.write(json.dumps({'run_id':sys.argv[2],'pod_id':sys.argv[3],'hourly_rate_usd':float(sys.argv[4]),'seconds':int(sys.argv[5]),'estimated_cost_usd':float(sys.argv[6]),'ended_epoch':int(time.time())})+'\n')
PYL
 fi
 touch "$state/main.done"; wait "$wd" 2>/dev/null || true
 python3 "$scripts/runpod_backend.py" zero | grep -qx true || { echo 'ERROR: RunPod final zero-pod check failed' >&2; rc=1; }
 return "$rc"
}
trap cleanup EXIT INT TERM
provision=$(python3 "$scripts/runpod_backend.py" create "$label" "$gpu" "$cap" "$disk_gb" COMMUNITY) || { echo 'RunPod create failed; label watchdog remains active' >&2; exit 1; }
printf '%s\n' "$provision" > "$private/runpod/create-responses/$run_id.summary.json"
pod_id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pod_id"])' "$provision")
hourly=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["hourly_rate_usd"])' "$provision")
pod_type=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["pod_type"])' "$provision")
printf '%s\n' "$pod_id" > "$state/pod-id.txt"
startup_end=$(( $(date +%s)+900 )); status_log="$private/runpod/startup-$run_id.jsonl"; mkdir -p "$(dirname "$status_log")"
while (( $(date +%s)<startup_end )); do
 raw=$(runpodctl pod get "$pod_id" --output json 2>/dev/null || true)
 state_json=$(python3 - "$raw" <<'PY'
import json,sys
try:x=json.loads(sys.argv[1])
except Exception: print('{}');raise SystemExit
if isinstance(x,list):x=x[0] if x else {}
print(json.dumps({'runtimeStatus':x.get('runtimeStatus','unknown'),'runtimeStatusReason':x.get('runtimeStatusReason',''),'desiredStatus':x.get('desiredStatus',''),'lastStatusChange':x.get('lastStatusChange','')}))
PY
)
printf '%s\n' "$state_json" >> "$status_log"
runtime_status=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("runtimeStatus","unknown"))' "$state_json")
[[ "$runtime_status" != running ]] || break
sleep 20
done
[[ "${runtime_status:-}" == running ]] || { echo 'RunPod startup timed out after 15 minutes' >&2; exit 1; }
ssh_raw=''; ssh_end=$(( $(date +%s)+900 ))
while (( $(date +%s)<ssh_end )); do
 ssh_raw=$(python3 "$scripts/runpod_backend.py" ssh-info "$pod_id" 2>/dev/null || true)
 read -r ssh_host ssh_port < <(python3 - "$ssh_raw" <<'PY'
import json,sys
try:x=json.loads(sys.argv[1]);print(x.get('host',''),x.get('port',''))
except Exception: pass
PY
)
[[ -n "$ssh_host" && -n "$ssh_port" ]] && break; sleep 10
done
[[ -n "$ssh_host" && -n "$ssh_port" ]] || { echo 'RunPod SSH endpoint unavailable' >&2; exit 1; }
ssh=(ssh -i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$ssh_port" "root@$ssh_host"); scp_opts=(-i "$key" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -P "$ssh_port")
ready=0; while (( $(date +%s)<ssh_end )); do "${ssh[@]}" 'test -x /workspace/bin/llama-bench' >/dev/null 2>&1 && { ready=1; break; }; sleep 10; done
(( ready )) || { echo 'pinned image readiness timed out' >&2; exit 1; }
upload_verified() {
 local src=$1 dest=$2 exec_flag=${3:-false} sha
 sha=$(shasum -a 256 "$src" | awk '{print $1}')
 printf 'local_path=%s remote_path=%s sha256=%s\n' "$src" "$dest" "$sha" >> "$out/upload-paths.log"
 scp "${scp_opts[@]}" "$src" "root@$ssh_host:$dest"
 if [[ "$exec_flag" == true ]]; then "${ssh[@]}" "chmod +x $dest"; fi
 "${ssh[@]}" "test -f $dest && actual=\$(sha256sum $dest | awk '{print \$1}') && test \"\$actual\" = $sha" || { echo "upload verification failed: $dest" >&2; return 1; }
}
upload_verified "$scripts/onstart.sh" /workspace/onstart.sh true
"${ssh[@]}" 'bash /workspace/onstart.sh' 
upload_verified "$scripts/preflight.sh" /workspace/preflight.sh true
transfer_model=$(python3 - <<'PY'
import yaml
x=yaml.safe_load(open('harness/registry/models.yaml'))['preflight_transfer'];print('\t'.join([x['url'],str(x['size_bytes']),x['sha256']]))
PY
); IFS=$'\t' read -r transfer_url transfer_size transfer_sha <<< "$transfer_model"
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then policy=smoke; else policy=card; fi
"${ssh[@]}" "bash /workspace/preflight.sh '$gpu' '16' '4' '/workspace/output/hosts/runpod' '$policy' 'unknown' '32' '$transfer_url' '$transfer_size' '$transfer_sha' '12.2.2' '535.54.03'" || { echo 'RunPod preflight failed' >&2; exit 1; }
upload_verified "$scripts/telemetry.sh" /workspace/telemetry.sh true
"${ssh[@]}" 'nohup bash /workspace/telemetry.sh /workspace/output/hosts/runpod/telemetry.csv >/workspace/output/hosts/runpod/telemetry.log 2>&1 </dev/null &'
if [[ "$mode" == smoke || "$mode" == smoke-kill ]]; then
  upload_verified "$scripts/gpu_bench.sh" /workspace/gpu_bench.sh true
  "${ssh[@]}" 'mkdir -p /workspace/output; curl -fL --retry 3 -o /workspace/tinyllama.gguf https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories15M-q4_0.gguf'
  if [[ "$mode" == smoke-kill ]]; then
    touch "$out/kill-test-running"
    "${ssh[@]}" 'sleep 300; /workspace/gpu_bench.sh /workspace/tinyllama.gguf /workspace/output/smoke-bench.json /workspace/output/smoke-bench.log -ngl 999 -p 512 -n 128 -r 1 -o json -v'
  else
    "${ssh[@]}" '/workspace/gpu_bench.sh /workspace/tinyllama.gguf /workspace/output/smoke-bench.json /workspace/output/smoke-bench.log -ngl 999 -p 512 -n 128 -r 1 -o json -v'
  fi
else
  scp -r "${scp_opts[@]}" "$repo/harness" root@"$ssh_host":/workspace/harness
  while IFS= read -r -d '' path; do localfile="$repo/$path"; remotefile="/workspace/$path"; expected=$(shasum -a 256 "$localfile" | awk '{print $1}'); "${ssh[@]}" "test -f $remotefile && actual=\$(sha256sum $remotefile | awk '{print \$1}') && test \"\$actual\" = $expected" || { echo "upload verification failed: $remotefile" >&2; exit 1; }; printf 'verified remote_path=%s sha256=%s\n' "$remotefile" "$expected" >> "$out/upload-paths.log"; done < <(git -C "$repo" ls-files -z -- harness/)
  "${ssh[@]}" "PATH=/workspace/bin:\$PATH LD_LIBRARY_PATH=/workspace/llama.cpp/build/bin python3 /workspace/harness/scripts/card_runner.py --card-id '$card_id' --run-id '$run_id' --host-id runpod --instance-cpu-ram '' --verified-status 'runpod' --provider runpod --pod-type '$pod_type' --hourly-rate '$hourly' --mode full"
fi
copy_dest="$out"; [[ "$mode" == card ]] || copy_dest="$out/results"; mkdir -p "$copy_dest"
scp "${scp_opts[@]}" -r "root@$ssh_host:/workspace/output/." "$copy_dest/"
pulled=1
if [[ "$mode" == card ]]; then
 end=$(date +%s); seconds=$((end-started)); end_utc=$(date -u +%FT%TZ)
 python3 - "$out" "$run_id" "$card_id" "$pod_id" "$label" "$hourly" "$pod_type" "$start_utc" "$end_utc" "$seconds" "$image" <<'PY'
import json,pathlib,subprocess,sys,yaml
out,run_id,card_id,pod_id,label,hourly,pod_type,start,end,seconds,image=sys.argv[1:]
cfg=yaml.safe_load(open('harness/registry/runtime.yaml'))
meta={'card_id':card_id,'run_id':run_id,'tier':next(c['tier'] for c in yaml.safe_load(open('harness/registry/cards.yaml'))['cards'] if c['card_id']==card_id),'provider':'runpod','pod_type':pod_type,'hourly_rate_usd':float(hourly),'llama_cpp_commit':cfg['llama_cpp_commit'],'image_digest':cfg['image_digest'],'image':image,'cuda_toolkit':cfg.get('cuda_toolkit'),'minimum_driver_version':cfg.get('minimum_driver_version'),'harness_git_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'schema_version':'1.6','methodology_version':'v1.3','hosts_used':[{'host_id':pod_id,'verified_status':'runpod'}],'instances':[{'pod_id':pod_id,'label':label,'pod_type':pod_type,'hourly_rate_usd':float(hourly),'actual_cost_usd':round(float(hourly)*int(seconds)/3600,6)}],'wall_clock':{'start_utc':start,'end_utc':end,'seconds':int(seconds)},'cost_usd':round(float(hourly)*int(seconds)/3600,6),'host_replication':{'status':'scheduled_second_run','eligible_host_count':None}}
pathlib.Path(out,'run.json').write_text(json.dumps(meta,indent=2)+'\n')
PY
fi
