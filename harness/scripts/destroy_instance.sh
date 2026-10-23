#!/usr/bin/env bash
set -euo pipefail
id=${1:?usage: destroy_instance.sh INSTANCE_ID LABEL [LOG]}
label=${2:?LABEL}
log=${3:-/dev/null}
attempt=1
while (( attempt <= 4 )); do
  printf 'destroy attempt %d/4 id=%s label=%s\n' "$attempt" "$id" "$label" >> "$log"
  if ! vastai destroy instance "$id" --yes --raw >/dev/null 2>&1; then
    printf 'destroy command failed for id=%s attempt=%d\n' "$id" "$attempt" >> "$log"
  fi
  for _ in $(seq 1 36); do
    instances=$(vastai show instances --raw 2>/dev/null) || { sleep 5; continue; }
    state=$(python3 - "$instances" "$id" "$label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
x=next((x for x in r if str(x.get('id'))==sys.argv[2]),None)
if x is None: print('absent')
elif str(x.get('label',''))==sys.argv[3]: print('present')
else: print('mismatch')
PY
)
    if [[ "$state" == absent ]]; then
      printf 'destroy confirmed absent id=%s\n' "$id" >> "$log"
      exit 0
    fi
    if [[ "$state" == mismatch ]]; then
      printf 'ERROR: refusing to retry destroy; instance id=%s has a different label\n' "$id" | tee -a "$log" >&2
      exit 1
    fi
    sleep 5
  done
  if (( attempt == 4 )); then
    printf 'ERROR: instance id=%s label=%s is still listed after 3 retries (4 destroy attempts)\n' "$id" "$label" | tee -a "$log" >&2
    exit 1
  fi
  printf 'instance still listed after 3-minute poll; retrying id=%s\n' "$id" >> "$log"
  ((attempt+=1))
done
