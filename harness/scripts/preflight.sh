#!/usr/bin/env bash
set -euo pipefail
expected_gpu="${1:-any}"
out="${2:-/workspace/hosts/$(hostname)}"
mkdir -p "$out"
nvidia-smi -q > "$out/nvidia-smi-q.txt"
lscpu > "$out/lscpu.txt"
cp /proc/meminfo "$out/meminfo.txt"
nvidia-smi --query-gpu=name,uuid,memory.total,pci.bus_id,pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max,power.limit,power.default_limit --format=csv,noheader > "$out/gpu-preflight.csv"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$out/gpu-processes.csv" || true
vmstat 1 2 > "$out/vmstat.txt"
python3 - "$expected_gpu" "$out/gpu-preflight.csv" "$out/gpu-processes.csv" "$out/vmstat.txt" <<'PY'
import csv, sys
expected, gpu_file, proc_file, vm_file = sys.argv[1:]
rows = list(csv.reader(open(gpu_file, newline="")))
if len(rows) != 1 or len(rows[0]) != 10:
    raise SystemExit("preflight failed: expected exactly one GPU and complete telemetry fields")
name, uuid, memory, bus, gen_current, gen_max, width_current, width_max, power, default = [x.strip() for x in rows[0]]
normalized = name.removeprefix("NVIDIA GeForce ").removeprefix("GeForce ")
if expected != "any" and normalized != expected:
    raise SystemExit(f"preflight failed: expected {expected}; found {name}")
if int(gen_max) < 3 or int(width_max) != 16 or int(width_current) != 16:
    raise SystemExit(f"preflight failed: PCIe link must support gen 3+ at x16 (found gen {gen_max}, width {width_current})")
if power in ("N/A", "[Not Supported]") or default in ("N/A", "[Not Supported]") or abs(float(power.split()[0]) - float(default.split()[0])) > 0.5:
    raise SystemExit("preflight failed: power limit differs from the factory default or is unavailable")
processes = [line.strip() for line in open(proc_file) if line.strip() and "No running processes found" not in line]
if processes:
    raise SystemExit("preflight failed: another process is using the GPU")
lines = [line.split() for line in open(vm_file) if line.strip()]
if len(lines) < 3:
    raise SystemExit("preflight failed: could not read CPU idle")
idle = float(lines[-1][-3])
if idle < 90.0:
    raise SystemExit(f"preflight failed: CPU busy above 10% (idle {idle:.1f}%)")
print(f"preflight ok: {name}, PCIe gen max {gen_max} x{width_current}, stock power limit, no GPU clients, CPU idle {idle:.1f}%")
PY
