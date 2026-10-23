#!/usr/bin/env bash
set -euo pipefail
id=${1:?usage: teardown.sh INSTANCE_ID LABEL [OUT_DIR]}
label=${2:?LABEL}
out=${3:-.}
script_dir=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$out"
instances=$(vastai show instances --raw 2>/dev/null) || { echo 'unable to query instances for teardown' >&2; exit 1; }
identity=$(python3 - "$instances" "$id" "$label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
x=next((x for x in r if str(x.get('id'))==sys.argv[2]),None)
if x is None: print('absent')
elif str(x.get('label',''))==sys.argv[3]: print('match')
else: print('mismatch')
PY
)
if [[ "$identity" == match ]]; then
  "$script_dir/destroy_instance.sh" "$id" "$label" "$out/teardown.log"
elif [[ "$identity" == mismatch ]]; then
  echo "refused to destroy mismatched instance id=$id; cleaning by label=$label" >> "$out/teardown.log"
fi
"$script_dir/destroy_label.sh" "$label" "$out/teardown.log"
"$script_dir/cleanup_orphans.sh" "$label" "$out/cleanup.log"
for _ in $(seq 1 24); do
  instances=$(vastai show instances --raw 2>/dev/null) || { sleep 5; continue; }
  present=$(python3 - "$instances" "$label" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
print(sum(1 for x in r if str(x.get('label',''))==sys.argv[2]))
PY
)
  [[ "$present" == 0 ]] && break
  "$script_dir/destroy_label.sh" "$label" "$out/teardown.log"
done
[[ "${present:-1}" == 0 ]] || { echo "run label $label remains after teardown" >&2; exit 1; }
"$script_dir/cleanup_orphans.sh" '' "$out/final-cleanup.log"
instances=$(vastai show instances --raw 2>/dev/null) || { echo 'final instance listing failed' >&2; exit 1; }
remaining=$(python3 - "$instances" <<'PY'
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
rows=[x for x in r if str(x.get('label','')).startswith('vtm-')]
for x in rows: print(f"leftover id={x.get('id')} label={x.get('label')}",file=sys.stderr)
print(len(rows))
PY
)
[[ "$remaining" == 0 ]] || { echo "final check found $remaining vtm-labeled instance(s)" >&2; exit 1; }
echo "destroyed run label $label; final vtm instance count 0"
[[ "$identity" != mismatch ]] || exit 1
