#!/usr/bin/env bash
set -euo pipefail
label=${1:?LABEL} owner=${2:?OWNER_PID} deadline=${3:?EPOCH_DEADLINE} state=${4:?STATE_DIR}
script_dir=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$state"
while :; do
  now=$(date +%s)
  instances=$(vastai show instances --raw 2>/dev/null) || { sleep 10; continue; }
  own_present=0
  if [[ -n "${instances//[[:space:]]/}" ]]; then
    own_present=1
    python3 - "$instances" "$label" <<'PY' || own_present=0
import json,sys
r=json.loads(sys.argv[1]); r=r if isinstance(r,list) else r.get('instances',[])
raise SystemExit(0 if any(str(x.get('label',''))==sys.argv[2] for x in r) else 1)
PY
  fi
  if { [[ -f "$state/active-instance-id.txt" ]] && (( own_present == 0 )); } || { [[ -f "$state/main.done" ]] && (( own_present == 0 )); }; then
    echo "watchdog run instances absent; exiting cleanly $(date -u +%FT%TZ)" >> "$state/watchdog.log"
    exit 0
  fi
  if (( now >= deadline )) || ! kill -0 "$owner" 2>/dev/null || [[ -f "$state/main.done" ]]; then
    echo "watchdog cleanup requested $(date -u +%FT%TZ)" >> "$state/watchdog.log"
    "$script_dir/cleanup_orphans.sh" "$label" "$state/orphan-cleanup.log" >> "$state/watchdog.log" 2>&1 || true
    while :; do
      id=''
      [[ -f "$state/active-instance-id.txt" ]] && id=$(head -n 1 "$state/active-instance-id.txt")
      if [[ -n "$id" ]]; then
        if "$script_dir/teardown.sh" "$id" "$label" "$state" >> "$state/watchdog.log" 2>&1; then exit 0; fi
      else
        if "$script_dir/destroy_label.sh" "$label" "$state/watchdog.log" >> "$state/watchdog.log" 2>&1 && "$script_dir/cleanup_orphans.sh" '' "$state/watchdog.log" >> "$state/watchdog.log" 2>&1; then exit 0; fi
      fi
      echo "watchdog cleanup retry $(date -u +%FT%TZ)" >> "$state/watchdog.log"
      sleep 5
    done
  fi
  sleep 10
done
