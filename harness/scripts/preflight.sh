#!/usr/bin/env bash
set -euo pipefail
expected_gpu="${1:-any}"
expected_width="${2:-16}"
native_gen="${3:-4}"
out="${4:-/workspace/hosts/$(hostname)}"
mkdir -p "$out"
nvidia-smi -q > "$out/nvidia-smi-q.txt"
lscpu > "$out/lscpu.txt"
cp /proc/meminfo "$out/meminfo.txt"
nvidia-smi --query-gpu=name,uuid,memory.total,pci.bus_id,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max,power.limit,power.default_limit --format=csv,noheader > "$out/gpu-preflight.csv"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$out/gpu-processes.csv" || true
vmstat 1 2 > "$out/vmstat.txt"
python3 - "$expected_gpu" "$expected_width" "$native_gen" "$out/gpu-preflight.csv" "$out/gpu-processes.csv" "$out/vmstat.txt" <<'PY'
import csv, sys
def reject(message):
    print(message, file=sys.stderr)
    raise SystemExit(42)
expected, native_width, native_gen, gpu_file, proc_file, vm_file = sys.argv[1:]
native_width, native_gen = int(native_width), int(native_gen)
rows = list(csv.reader(open(gpu_file, newline="")))
if len(rows) != 1 or len(rows[0]) != 10:
    reject("preflight failed: expected exactly one GPU and complete telemetry fields")
name, uuid, memory, bus, gen_current, gen_max, width_current, width_max, power, default = [x.strip() for x in rows[0]]
normalized = name.removeprefix("NVIDIA GeForce ").removeprefix("NVIDIA ").removeprefix("GeForce ").removeprefix("Quadro ")
if expected != "any" and normalized != expected:
    reject(f"preflight failed: expected {expected}; found {name}")
actual_gen, actual_width = int(gen_current), int(width_current)
if actual_gen < 3 or actual_width != native_width:
    reject(f"preflight failed: PCIe needs gen >=3 and native width x{native_width}; found gen {actual_gen} width x{actual_width}")
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
print(f"pcie_actual_gen={actual_gen} pcie_actual_width={actual_width} pcie_native_gen={native_gen} pcie_native_width={native_width}")
if actual_gen == 3 and native_gen == 4:
    print("FLAG: PCIe Gen 3 link on a Gen 4 card")
print(f"preflight ok: {name}, PCIe gen {actual_gen} x{actual_width}, stock power limit, no GPU clients, CPU idle {idle:.1f}%")
PY
