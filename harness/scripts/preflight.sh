#!/usr/bin/env bash
set -euo pipefail
expected_gpu="${1:-any}"
native_width="${2:-16}"
native_gen="${3:-4}"
out="${4:-/workspace/hosts/$(hostname)}"
mkdir -p "$out"
nvidia-smi -q > "$out/nvidia-smi-q.txt"
lscpu > "$out/lscpu.txt"
if [[ -r /proc/meminfo ]]; then cp /proc/meminfo "$out/meminfo.txt"; else printf 'MemTotal: unavailable\n' > "$out/meminfo.txt"; fi
nvidia-smi --query-gpu=name,uuid,memory.total,pci.bus_id,power.limit,power.default_limit --format=csv,noheader,nounits > "$out/gpu-info.csv"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$out/gpu-processes.csv" || true
vmstat 1 2 > "$out/vmstat.txt"
pcie_csv="$out/gpu-preflight.csv"
printf 'timestamp_utc,phase,pcie_gen_current,pcie_gen_max,pcie_gen_hostmax,pcie_width_current,pcie_width_max\n' > "$pcie_csv"
sample_pcie() {
  local phase="$1" stamp values gen width genmax widthmax hostmax
  stamp=$(date -u +%FT%TZ)
  values=$(nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv,noheader,nounits 2>/dev/null | head -n 1 || true)
  IFS=, read -r gen genmax width widthmax <<< "$values"
  gen=${gen//[[:space:]]/}; genmax=${genmax//[[:space:]]/}; width=${width//[[:space:]]/}; widthmax=${widthmax//[[:space:]]/}
  hostmax=$(nvidia-smi --query-gpu=pcie.link.gen.hostmax --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
  [[ -n "$hostmax" ]] || hostmax=N/A
  [[ -n "$gen" ]] || gen=N/A; [[ -n "$genmax" ]] || genmax=N/A
  [[ -n "$width" ]] || width=N/A; [[ -n "$widthmax" ]] || widthmax=N/A
  printf '%s,%s,%s,%s,%s,%s,%s\n' "$stamp" "$phase" "$gen" "$genmax" "$hostmax" "$width" "$widthmax" >> "$pcie_csv"
}
sample_pcie idle

python3 - "$expected_gpu" "$native_width" "$native_gen" "$out/gpu-info.csv" "$out/gpu-processes.csv" "$out/vmstat.txt" <<'PY'
import csv, sys
def reject(message):
    print(message, file=sys.stderr)
    raise SystemExit(42)
expected, native_width, native_gen, gpu_file, proc_file, vm_file = sys.argv[1:]
native_width, native_gen = int(native_width), int(native_gen)
rows = list(csv.reader(open(gpu_file, newline="")))
if len(rows) != 1 or len(rows[0]) != 6:
    reject("preflight failed: expected exactly one GPU and complete identity/power fields")
name, uuid, memory, bus, power, default = [x.strip() for x in rows[0]]
normalized = name.removeprefix("NVIDIA GeForce ").removeprefix("NVIDIA ").removeprefix("GeForce ").removeprefix("Quadro ")
if expected != "any" and normalized != expected:
    reject(f"preflight failed: expected {expected}; found {name}")
if power in ("N/A", "[Not Supported]") or default in ("N/A", "[Not Supported]") or abs(float(power.split()[0]) - float(default.split()[0])) > 0.5:
    reject("preflight failed: power limit differs from the factory default or is unavailable")
processes = [line.strip() for line in open(proc_file) if line.strip() and "No running processes found" not in line]
if processes:
    reject("preflight failed: another process is using the GPU")
lines = [line.split() for line in open(vm_file) if line.strip()]
if len(lines) < 3:
    reject("preflight failed: could not read CPU idle")
idle = float(lines[-1][-3])
if idle < 90.0:
    reject(f"preflight failed: CPU busy above 10% (idle {idle:.1f}%)")
print(f"GPU identity/power/process/CPU precheck passed: {name}; CPU idle {idle:.1f}%")
PY

model="${VTM_PREFLIGHT_MODEL:-/workspace/tinyllama.gguf}"
if [[ ! -s "$model" ]]; then
  curl -fL --retry 3 --retry-delay 2 -o "$model" https://huggingface.co/ggml-org/models/resolve/main/tinyllamas/stories15M-q4_0.gguf
fi
bench="${VTM_LLAMA_BENCH:-/workspace/bin/llama-bench}"
[[ -x "$bench" ]] || { echo 'preflight failed: pinned llama-bench missing or not executable' >&2; exit 43; }
bench_log="$out/pcie-load-bench.log"
deadline=$((SECONDS + ${VTM_PCIE_LOAD_SECONDS:-10}))
while (( SECONDS < deadline )); do
  "$bench" -m "$model" -ngl 999 -p 512 -n 128 -r 20 >> "$bench_log" 2>&1 &
  bench_pid=$!
  while kill -0 "$bench_pid" 2>/dev/null && (( SECONDS < deadline )); do
    sample_pcie load
    sleep 0.2
  done
  if kill -0 "$bench_pid" 2>/dev/null; then
    kill -TERM "$bench_pid" 2>/dev/null || true
    wait "$bench_pid" 2>/dev/null || true
    break
  fi
  wait "$bench_pid" || true
done

python3 - "$pcie_csv" "$native_width" "$native_gen" <<'PY'
import csv, sys
path, native_width, native_gen = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
rows = list(csv.DictReader(open(path, newline="")))
def number(value):
    try: return int(value)
    except (TypeError, ValueError): return None
idle = next((row for row in rows if row['phase'] == 'idle'), None)
loads = [row for row in rows if row['phase'] == 'load']
if not idle or not loads:
    print('preflight failed: no PCIe samples captured during GPU load', file=sys.stderr)
    raise SystemExit(42)
idle_gen, idle_width = number(idle['pcie_gen_current']), number(idle['pcie_width_current'])
max_gens = [number(row['pcie_gen_max']) for row in rows]
hostmaxes = [number(row['pcie_gen_hostmax']) for row in rows]
max_widths = [number(row['pcie_width_max']) for row in rows]
max_gen = max((v for v in max_gens if v is not None), default=None)
hostmax = max((v for v in hostmaxes if v is not None), default=None)
max_width = max((v for v in max_widths if v is not None), default=None)
valid = [(number(row['pcie_gen_current']), number(row['pcie_width_current'])) for row in loads]
valid = [(gen, width) for gen, width in valid if gen is not None and width is not None]
under = next(((gen, width) for gen, width in valid if gen >= 3 and width == native_width), None)
print(f'pcie_idle_gen={idle_gen} pcie_idle_width={idle_width} pcie_gen_max={max_gen} pcie_gen_hostmax={hostmax if hostmax is not None else "N/A"} pcie_width_max={max_width}')
print('pcie_under_load_samples=' + ';'.join(f'gen{gen}x{width}' for gen, width in valid))
if (max_gen is not None and max_gen < 3) or (hostmax is not None and hostmax < 3):
    print(f'preflight failed: slot maximum cannot reach PCIe Gen 3 (gen.max={max_gen}, gen.hostmax={hostmax})', file=sys.stderr)
    raise SystemExit(42)
if max_width is not None and max_width < native_width:
    print(f'preflight failed: slot maximum width x{max_width} cannot provide native x{native_width}', file=sys.stderr)
    raise SystemExit(42)
if not under:
    observed = ', '.join(f'gen {gen} x{width}' for gen, width in valid) or 'no valid samples'
    print(f'preflight failed: under-load PCIe needs gen >=3 and native width x{native_width}; observed {observed}', file=sys.stderr)
    raise SystemExit(42)
if under[0] == 3 and native_gen == 4:
    print('FLAG: PCIe Gen 3 link on a Gen 4 card')
print(f'preflight ok: under-load PCIe gen {under[0]} x{under[1]} (native x{native_width})')
PY
