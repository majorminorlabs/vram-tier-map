#!/usr/bin/env bash
set -euo pipefail
model=${1:?usage: bench.sh MODEL OUTPUT_DIR [repetitions] [llama-bench flags...]}
out=${2:?usage: bench.sh MODEL OUTPUT_DIR [repetitions] [llama-bench flags...]}
reps=${3:-5}
shift 3 || true
extra=("$@")
mkdir -p "$out"
command -v llama-bench >/dev/null || { echo 'llama-bench is missing' >&2; exit 1; }
depths=${BENCH_DEPTHS:-'65536 122880'}
for depth in $depths; do
  attempt=1
  while (( attempt <= 3 )); do
    file="$out/bench-depth-${depth}-attempt${attempt}.json"
    llama-bench -m "$model" "${extra[@]}" -p 512 -n 128 -d "$depth" -r "$reps" -o json > "$file"
    cv=$(python3 - "$file" <<'PY'
import json,sys
x=json.load(open(sys.argv[1])); rows=x if isinstance(x,list) else x.get('results',[])
vals=[]
for r in rows:
 avg=r.get('avg_ts',r.get('mean_ts',r.get('tokens_per_second')))
 sd=r.get('stddev_ts',r.get('std_ts'))
 if avg and sd is not None: vals.append(float(sd)/float(avg)*100)
print(max(vals) if vals else 0)
PY
)
    awk -v cv="$cv" 'BEGIN{exit !(cv>5)}' || break
    echo "coefficient of variation ${cv}% at depth ${depth}; rerunning" >&2
    ((attempt+=1))
  done
  printf '%s\n' "$cv" > "$out/bench-depth-${depth}-cv-percent.txt"
done
