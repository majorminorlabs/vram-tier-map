#!/usr/bin/env bash
set -euo pipefail
mode=${1:?usage: run_provider.sh mode GPU_NAME OUTPUT_DIR RUN_ID}
gpu=${2:?GPU_NAME}; out=${3:?OUTPUT_DIR}; run_id=${4:-}
repo=$(cd "$(dirname "$0")/../.." && pwd); cd "$repo"
provider=$(python3 - "$gpu" <<'PY'
import json,sys
rows=json.load(open('harness/registry/cards.yaml'))['cards']
card=next((x for x in rows if x['gpu_name']==sys.argv[1]),None)
if not card: raise SystemExit('GPU is absent from card registry')
print(card.get('provider','vast'))
PY
)
case "$provider" in
 runpod) exec "$repo/harness/scripts/runpod_run.sh" "$mode" "$gpu" "$out" "$run_id" ;;
 vast) exec "$repo/harness/scripts/vast_run.sh" "$mode" "$gpu" 0.30 "$out" "$run_id" ;;
 *) echo "unsupported provider: $provider" >&2; exit 2 ;;
esac
