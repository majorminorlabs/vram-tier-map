#!/usr/bin/env bash
set -euo pipefail
label=${1:?LABEL}
log=${2:-/dev/null}
instances=$(vastai show instances --raw 2>/dev/null) || { echo 'instance listing failed' >> "$log"; exit 1; }
while IFS= read -r id; do
  [[ -n "$id" ]] || continue
  echo "destroying label match id=$id label=$label" >> "$log"
  vastai destroy instance "$id" --raw >/dev/null 2>&1 || true
done < <(python3 - "$instances" "$label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
for x in r:
 if str(x.get('label',''))==sys.argv[2]: print(x.get('id',''))
PY
)
for _ in $(seq 1 24); do
  instances=$(vastai show instances --raw 2>/dev/null) || { sleep 5; continue; }
  count=$(python3 - "$instances" "$label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
print(sum(1 for x in r if str(x.get('label',''))==sys.argv[2]))
PY
)
  [[ "$count" == 0 ]] && exit 0
  sleep 5
done
echo "label $label remains after cleanup deadline" >> "$log"
exit 1
