#!/usr/bin/env bash
set -euo pipefail
expected_gpu="${1:-any}"
native_width="${2:-16}"
native_gen="${3:-4}"
out="${4:-/workspace/hosts/$(hostname)}"
policy="${5:-card}"
instance_cpu_ram="${6:-unknown}"
ram_floor_gb="${7:-32}"
transfer_url="${8:-https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/5ab33fa94d1d04e903623ae72c95d1696f09f9e8/Llama-3.2-3B-Instruct-Q4_K_M.gguf}"
transfer_size="${9:-2019377696}"
transfer_sha="${10:-6c1a2b41161032677be168d354123594c0e6e67d2b9227c84f296ad037c728ff}"
required_cuda="${11:-12.2.2}"
minimum_driver="${12:-535.54.03}"
mkdir -p "$out"
nvidia-smi -q > "$out/nvidia-smi-q.txt"
python3 - "$out/nvidia-smi-q.txt" "$required_cuda" "$minimum_driver" "$out/host-driver.json" <<'PY'
import json,re,sys
def version(value):
    m=re.search(r'\d+(?:\.\d+)+',value or '')
    return tuple(map(int,m.group(0).split('.'))) if m else None
def at_least(a,b):
    a,b=version(a),version(b)
    if a is None or b is None: return False
    n=max(len(a),len(b)); return a+(0,)*(n-len(a)) >= b+(0,)*(n-len(b))
path,target,driver_floor,out=sys.argv[1:]
text=open(path,errors='replace').read()
driver=re.search(r'^Driver Version\s*:\s*(\S+)',text,re.M)
cuda=re.search(r'^CUDA Version\s*:\s*(\S+)',text,re.M)
driver=driver.group(1) if driver else None; cuda=cuda.group(1) if cuda else None
cuda_floor='.'.join(target.split('.')[:2])
record={'driver_version':driver,'host_cuda_max':cuda,'required_cuda_toolkit':target,'minimum_driver_version':driver_floor,'driver_pass':at_least(driver,driver_floor),'cuda_pass':at_least(cuda,cuda_floor)}
json.dump(record,open(out,'w'),indent=2); open(out,'a').write('\n')
print(f"host NVIDIA driver={driver or 'unknown'} max CUDA={cuda or 'unknown'} required CUDA={target} driver floor={driver_floor}")
if not record['driver_pass'] or not record['cuda_pass']:
    print('preflight failed: NVIDIA driver or max supported CUDA is below the pinned image requirement',file=sys.stderr)
    raise SystemExit(42)
PY
lscpu > "$out/lscpu.txt"
if [[ -r /proc/meminfo ]]; then cp /proc/meminfo "$out/meminfo.txt"; else printf 'MemTotal: unavailable\n' > "$out/meminfo.txt"; fi
nvidia-smi --query-gpu=name,uuid,memory.total,pci.bus_id,power.limit,power.default_limit --format=csv,noheader,nounits > "$out/gpu-info.csv"
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader > "$out/gpu-processes.csv" || true
vmstat 1 2 > "$out/vmstat.txt"
pcie_csv="$out/gpu-preflight.csv"
printf 'timestamp_utc,phase,transfer_index,elapsed_seconds,transfer_bytes,pcie_gen_current,pcie_gen_max,pcie_gen_hostmax,pcie_width_current,pcie_width_max,pcie_width_hostmax,gpu_util_percent,pstate,power_limit_w,power_default_limit_w,h2d_gbps\n' > "$pcie_csv"
transfer_start_ns=0
transfer_bytes=0
transfer_index=0
sample_pcie() {
  local phase="$1" stamp values gen genmax width widthmax hostgen hostwidth telemetry util pstate power default elapsed
  stamp=$(date -u +%FT%TZ)
  values=$(nvidia-smi --query-gpu=pcie.link.gen.current,pcie.link.gen.max,pcie.link.width.current,pcie.link.width.max --format=csv,noheader,nounits 2>/dev/null | head -n 1 || true)
  IFS=, read -r gen genmax width widthmax <<< "$values"
  gen=${gen//[[:space:]]/}; genmax=${genmax//[[:space:]]/}; width=${width//[[:space:]]/}; widthmax=${widthmax//[[:space:]]/}
  hostgen=$(nvidia-smi --query-gpu=pcie.link.gen.hostmax --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
  hostwidth=$(nvidia-smi --query-gpu=pcie.link.width.hostmax --format=csv,noheader,nounits 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)
  telemetry=$(nvidia-smi --query-gpu=utilization.gpu,pstate,power.limit,power.default_limit --format=csv,noheader,nounits 2>/dev/null | head -n 1 || true)
  IFS=, read -r util pstate power default <<< "$telemetry"
  for value in gen genmax width widthmax hostgen hostwidth util pstate power default; do
    local cleaned="${!value//[[:space:]]/}"
    [[ -n "$cleaned" ]] || cleaned=N/A
    printf -v "$value" '%s' "$cleaned"
  done
  elapsed=''
  if [[ "$phase" == transfer && "$transfer_start_ns" != 0 ]]; then
    local now_ns
    now_ns=$(date +%s%N)
    elapsed=$(awk -v a="$now_ns" -v b="$transfer_start_ns" 'BEGIN {printf "%.6f", (a-b)/1000000000}')
  fi
  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,\n' \
    "$stamp" "$phase" "$transfer_index" "$elapsed" "$transfer_bytes" "$gen" "$genmax" "$hostgen" "$width" "$widthmax" "$hostwidth" "$util" "$pstate" "$power" "$default" >> "$pcie_csv"
}
sample_pcie idle

python3 - "$expected_gpu" "$policy" "$ram_floor_gb" "$instance_cpu_ram" "$out/gpu-info.csv" "$out/gpu-processes.csv" "$out/vmstat.txt" "$out/meminfo.txt" "$out/host-quality.json" "$out/host-driver.json" <<'PY'
import csv, json, sys
def fail(message):
    print(message, file=sys.stderr)
    raise SystemExit(42)
expected, policy, ram_floor, cpu_ram, gpu_file, proc_file, vm_file, mem_file, quality_file, driver_file = sys.argv[1:]
ram_floor = float(ram_floor)
rows = list(csv.reader(open(gpu_file, newline="")))
if len(rows) != 1 or len(rows[0]) != 6:
    fail("preflight failed: expected exactly one GPU and complete identity/power fields")
name, uuid, memory, bus, power, default = [x.strip() for x in rows[0]]
normalized = name.removeprefix("NVIDIA GeForce ").removeprefix("NVIDIA ").removeprefix("GeForce ").removeprefix("Quadro ")
if expected != "any" and normalized != expected:
    fail(f"preflight failed: expected {expected}; found {name}")
def number(text):
    try: return float(text.strip().split()[0])
    except (TypeError, ValueError, IndexError): return None
power_w, default_w = number(power), number(default)
power_ok = power_w is not None and default_w not in (None, 0) and power_w >= .90 * default_w
processes = [line.strip() for line in open(proc_file) if line.strip() and "No running processes found" not in line]
if processes: fail("preflight failed: another process is using the GPU")
lines = [line.split() for line in open(vm_file) if line.strip()]
if len(lines) < 3: fail("preflight failed: could not read CPU idle")
idle = float(lines[-1][-3])
if idle < 90.0: fail(f"preflight failed: CPU busy above 10% (idle {idle:.1f}%)")
mem = {}
for line in open(mem_file):
    if ':' in line:
        k,v=line.split(':',1)
        try: mem[k]=int(v.strip().split()[0])/1048576
        except (ValueError, IndexError): pass
total_gb=mem.get('MemTotal'); free_gb=mem.get('MemAvailable')
try: offer_ram_gb=float(cpu_ram)/1000
except (TypeError, ValueError): offer_ram_gb=None
ram_ok=offer_ram_gb is not None and offer_ram_gb >= ram_floor
driver=json.load(open(driver_file))
quality={'gpu_name':name,'power_limit_w':power_w,'power_default_limit_w':default_w,'power_ratio':power_w/default_w if power_w is not None and default_w else None,'power_pass':power_ok,'host_ram_total_gb':total_gb,'free_ram_at_preflight_gb':free_gb,'instance_cpu_ram':cpu_ram,'ram_floor_gb':ram_floor,'ram_pass':ram_ok,'cpu_idle_percent':idle,'driver_version':driver['driver_version'],'host_cuda_max':driver['host_cuda_max'],'required_cuda_toolkit':driver['required_cuda_toolkit']}
with open(quality_file,'w') as f: json.dump(quality,f,indent=2); f.write('\n')
if policy in ('smoke','smoke-kill'):
    if not power_ok: print(f"WARNING: smoke host power quality below 90% default or unavailable ({power}/{default}); continuing")
    if not ram_ok: print(f"WARNING: smoke host RAM offer below {ram_floor:g} GB or unavailable (cpu_ram={cpu_ram}); continuing")
else:
    if not power_ok: fail(f"preflight failed: power limit {power_w}W is below 90% of default {default_w}W")
print(f"GPU identity/process/CPU checks passed: {name}; CPU idle {idle:.1f}%")
print(f"host RAM total={total_gb} GiB free={free_gb} GiB instance_cpu_ram={cpu_ram} MB policy={policy}")
PY

model="${VTM_PREFLIGHT_MODEL:-/workspace/preflight-transfer.gguf}"
if [[ "$policy" == smoke || "$policy" == smoke-kill ]]; then
  echo 'WARN: smoke mode records PCIe capability without a host-quality bandwidth gate' | tee "$out/pcie-transfer-warning.txt"
  python3 - "$out/host-quality.json" "$out/gpu-preflight.csv" "$native_width" <<'PY'
import csv,json,sys
p,csv_path,native=sys.argv[1:]; q=json.load(open(p)); rows=list(csv.DictReader(open(csv_path)))
q.update({'pcie_h2d_gbps':None,'pcie_h2d_pass':None,'pcie_width_max':max([int(r['pcie_width_max']) for r in rows if r['pcie_width_max'].isdigit()],default=None),'pcie_width_hostmax':max([int(r['pcie_width_hostmax']) for r in rows if r['pcie_width_hostmax'].isdigit()],default=None),'pcie_smoke_check':'warn_only_not_measured'})
with open(p,'w') as f: json.dump(q,f,indent=2); f.write('\n')
PY
elif [[ ! -s "$model" ]]; then
  if ! curl -fL --retry 3 --retry-delay 2 -o "$model" "$transfer_url"; then
    echo 'WARNING: PCIe transfer model download failed' | tee "$out/pcie-transfer-warning.txt"
    [[ "$policy" == smoke ]] || exit 42
  fi
fi
transfer_result=failed
if [[ -s "$model" && "$policy" != smoke && "$policy" != smoke-kill ]]; then
  actual_size=$(wc -c < "$model" | tr -d '[:space:]')
  if [[ -n "${VTM_PREFLIGHT_MODEL:-}" ]]; then transfer_size="$actual_size"; transfer_sha=''; fi
  actual_sha=$(sha256sum "$model" | awk '{print $1}')
  if [[ "$actual_size" != "$transfer_size" || ( -n "$transfer_sha" && "$actual_sha" != "$transfer_sha" ) ]]; then
    echo "WARNING: PCIe transfer model size/hash mismatch (bytes=$actual_size)" | tee "$out/pcie-transfer-warning.txt"
    [[ "$policy" == smoke ]] || exit 42
  else
    bench="${VTM_LLAMA_BENCH:-/workspace/bin/llama-bench}"
    if [[ ! -x "$bench" ]]; then
      if [[ "$policy" == smoke || "$policy" == smoke-kill ]]; then echo 'WARNING: pinned llama-bench unavailable for PCIe quality sample' | tee "$out/pcie-transfer-warning.txt"; exit 0; fi
      echo 'preflight failed: pinned llama-bench missing or not executable' >&2; exit 43
    fi
    reps="${VTM_TRANSFER_REPS:-3}"
    [[ "$policy" == smoke ]] && reps="${VTM_TRANSFER_REPS:-1}"
    transfer_summary="$out/pcie-transfer.json"
    transfer_bytes_per_run="${VTM_PREFLIGHT_TRANSFER_BYTES:-$actual_size}"
    export transfer_summary transfer_size transfer_bytes_per_run
    for transfer_index in $(seq 1 "$reps"); do
      transfer_index=$transfer_index
      bench_log="$out/pcie-transfer-${transfer_index}.log"
      transfer_bytes="$transfer_bytes_per_run"
      transfer_start_ns=$(date +%s%N)
      started_utc=$(date -u +%FT%TZ)
      LD_LIBRARY_PATH="${LD_LIBRARY_PATH:+$LD_LIBRARY_PATH:}/workspace/llama.cpp/build/bin" "$bench" -m "$model" -ngl 99 -mmp 0 -p 1 -n 1 -r 1 -o json > "$bench_log" 2>&1 &
      bench_pid=$!
      sample_pcie transfer
      while kill -0 "$bench_pid" 2>/dev/null; do
        sample_pcie transfer
        sleep 0.05
      done
      bench_rc=0; wait "$bench_pid" || bench_rc=$?
      ended_utc=$(date -u +%FT%TZ)
      parsed=$(python3 - "$bench_log" "$transfer_bytes_per_run" "$bench_rc" "$started_utc" "$ended_utc" <<'PY'
import json,re,sys
path,size,rc,start,end=sys.argv[1:]
text=open(path,errors='replace').read()
loads=re.findall(r'load time\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)\s*ms',text,re.I)
offloads=re.findall(r'offloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+GPU',text,re.I)
load_ms=float(loads[-1]) if loads else None
gpu_layers,total_layers=(tuple(map(int,offloads[-1])) if offloads else (None,None))
rate=(int(size)/1e9)/(load_ms/1000) if load_ms and load_ms>0 else None
print(json.dumps({'start_utc':start,'end_utc':end,'bytes_moved':int(size),'load_time_ms':load_ms,'h2d_gbps':rate,'gpu_layers':gpu_layers,'total_layers':total_layers,'bench_exit':int(rc),'passed_offload':gpu_layers is not None and total_layers is not None and gpu_layers==total_layers}))
PY
)
      printf '%s\n' "$parsed" >> "$out/pcie-transfer-runs.jsonl"
      if (( bench_rc != 0 )); then transfer_result=failed; fi
    done
    python3 - "$out/pcie-transfer-runs.jsonl" "$native_width" "$policy" "$out/host-quality.json" "$transfer_summary" "$out/gpu-preflight.csv" <<'PY'
import csv,json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
native=int(sys.argv[2]); policy=sys.argv[3]; quality_path,summary_path,sample_path=sys.argv[4:]
quality=json.load(open(quality_path))
samples=list(csv.DictReader(open(str(__import__('pathlib').Path(sys.argv[1]).parent/'gpu-preflight.csv'))))
def num(x):
    try: return float(x)
    except (TypeError,ValueError): return None
valid=[r for r in rows if r['bench_exit']==0 and r['load_time_ms'] and r['passed_offload']]
total_bytes=sum(r['bytes_moved'] for r in valid)
total_seconds=sum(r['load_time_ms'] for r in valid)/1000
h2d=total_bytes/1e9/total_seconds if total_seconds else None
widthmaxes=[int(s['pcie_width_max']) for s in samples if s['pcie_width_max'].isdigit()]
hostwidths=[int(s['pcie_width_hostmax']) for s in samples if s['pcie_width_hostmax'].isdigit()]
width_ok=bool(widthmaxes) and all(v==native for v in widthmaxes) and (not hostwidths or all(v==native for v in hostwidths))
threshold=6 if native==16 else 5
pcie_ok=h2d is not None and h2d>=threshold and width_ok
quality.update({'pcie_width_max':max(widthmaxes) if widthmaxes else None,'pcie_width_hostmax':max(hostwidths) if hostwidths else None,'pcie_width_pass':width_ok,'pcie_h2d_gbps':h2d,'pcie_h2d_threshold_gbps':threshold,'pcie_h2d_pass':pcie_ok,'pcie_transfer_runs':rows})
with open(quality_path,'w') as f: json.dump(quality,f,indent=2); f.write('\n')
with open(summary_path,'w') as f: json.dump({'measured_h2d_gbps':h2d,'transfer_bytes_per_run':rows[0]['bytes_moved'] if rows else None,'load_time_ms_per_run':[r['load_time_ms'] for r in rows],'threshold_gbps':threshold,'width_pass':width_ok,'passed':pcie_ok,'runs':rows},f,indent=2); f.write('\n')
with open(sample_path,newline='') as f: sample_rows=list(csv.DictReader(f)); fields=list(sample_rows[0]) if sample_rows else []
for sample in sample_rows:
    if sample.get('phase')=='transfer': sample['h2d_gbps']='' if h2d is None else f'{h2d:.6f}'
with open(sample_path,'w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader(); writer.writerows(sample_rows)
if not pcie_ok:
    message=f"PCIe H2D check failed: measured={h2d} GB/s threshold={threshold} width.max/hostmax native={native}"
    if policy in ('smoke','smoke-kill'): print('WARNING: '+message)
    else: print('preflight failed: '+message,file=sys.stderr); raise SystemExit(42)
else: print(f"preflight PCIe traffic pass: H2D={h2d:.2f} GB/s; native width x{native}")
PY
  fi
fi
if [[ "$policy" == smoke || "$policy" == smoke-kill ]]; then
  echo 'smoke preflight host-quality checks are warn-only; continuing to the required smoke benchmark'
fi
