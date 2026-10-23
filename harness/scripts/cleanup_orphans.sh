#!/usr/bin/env bash
set -euo pipefail
active_label=${1:-}
log=${2:-/dev/null}
script_dir=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$(dirname "$log")"
list_instances() { vastai show instances --raw 2>/dev/null; }
instances=$(list_instances) || { echo 'instance listing failed' >> "$log"; exit 1; }
while IFS=$'\t' read -r id label; do
  [[ -n "$id" ]] || continue
  echo "destroying orphan id=$id label=$label" >> "$log"
  "$script_dir/destroy_instance.sh" "$id" "$label" "$log"
done < <(python3 - "$instances" "$active_label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
for x in r:
 label=str(x.get('label',''))
 if label.startswith('vtm-') and label != sys.argv[2]:
  print(f"{x.get('id','')}\t{label}")
PY
)
for _ in $(seq 1 24); do
  instances=$(list_instances) || { sleep 5; continue; }
  remaining=$(python3 - "$instances" "$active_label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
print(sum(1 for x in r if str(x.get('label','')).startswith('vtm-') and str(x.get('label',''))!=sys.argv[2]))
PY
)
  [[ "$remaining" == 0 ]] && exit 0
  sleep 5
done
echo 'orphan vtm instances remain after cleanup deadline' >> "$log"
exit 1
