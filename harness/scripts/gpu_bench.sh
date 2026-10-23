#!/usr/bin/env bash
set -euo pipefail
model=${1:?usage: gpu_bench.sh MODEL JSON_OUTPUT LOG_OUTPUT [llama-bench flags...]}
json_out=${2:?JSON_OUTPUT}
log_out=${3:?LOG_OUTPUT}
shift 3
mkdir -p "$(dirname "$json_out")" "$(dirname "$log_out")"
bench=${LLAMA_BENCH:-/workspace/bin/llama-bench}
command -v "$bench" >/dev/null || { echo 'llama-bench is missing' >&2; exit 1; }
gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1 | xargs)
[[ -n "$gpu_name" ]] || { echo 'nvidia-smi did not report a GPU name' >&2; exit 1; }
memory_log="${log_out%.log}-gpu-memory.csv"
baseline=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
[[ "$baseline" =~ ^[0-9]+$ ]] || { echo 'nvidia-smi did not report baseline GPU memory' >&2; exit 1; }
printf 'timestamp,gpu_name,memory_used_mib\n' > "$memory_log"
LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}/workspace/llama.cpp/build/bin" "$bench" -m "$model" "$@" > "$json_out" 2> "$log_out" &
bench_pid=$!
while kill -0 "$bench_pid" 2>/dev/null; do
  value=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || true)
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s,%s,%s\n' "$(date -u +%FT%TZ)" "$gpu_name" "$value" >> "$memory_log"
  fi
  sleep 0.2
done
rc=0
wait "$bench_pid" || rc=$?
python3 - "$log_out" "$memory_log" "$gpu_name" "$baseline" "$rc" <<'PY'
import csv,re,sys
log_path,memory_path,gpu_name,baseline,rc=sys.argv[1:]
log=open(log_path,errors='replace').read()
if int(rc): raise SystemExit(f'llama-bench failed with exit code {rc}')
if gpu_name not in log:
    raise SystemExit(f'llama-bench log does not identify the nvidia-smi GPU ({gpu_name})')
matches=re.findall(r'offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to GPU',log,re.I)
if not matches:
    raise SystemExit('llama-bench log has no GPU layer offload count')
gpu_layers,total_layers=map(int,matches[-1])
if total_layers < 1 or gpu_layers != total_layers:
    raise SystemExit(f'not all layers were offloaded to GPU: {gpu_layers}/{total_layers}')
with open(memory_path,newline='') as f:
    rows=list(csv.DictReader(f))
values=[int(r['memory_used_mib']) for r in rows]
peak=max(values,default=0)
if peak < int(baseline)+32:
    raise SystemExit(f'nvidia-smi GPU memory did not rise by at least 32 MiB while benchmarking (baseline={baseline}, peak={peak})')
print(f'GPU verification passed: {gpu_name}; all {gpu_layers} layers on GPU; nvidia-smi memory {baseline}->{peak} MiB')
PY
