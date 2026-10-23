#!/usr/bin/env bash
set -euo pipefail
out=${1:?usage: check_run_output.sh RUN_OUTPUT_DIR}
mkdir -p "$out"
# A retry after a rejected host may reuse its run directory. These files only
# identify the last rental and its verified uploads; benchmark results remain
# protected from overwrite.
other=$(find "$out" -mindepth 1 -maxdepth 1 \
  ! -name instance-id.txt ! -name label.txt ! -name upload-paths.log \
  -print -quit)
if [[ -n "$other" ]]; then
  echo 'refusing to overwrite an existing committed or partial run directory; use a new run_id' >&2
  exit 2
fi
