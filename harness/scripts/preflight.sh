#!/usr/bin/env bash
set -euo pipefail
command -v nvidia-smi >/dev/null || { echo 'nvidia-smi is required' >&2; exit 1; }
nvidia-smi --query-gpu=name,memory.total,uuid --format=csv
command -v python3 >/dev/null || { echo 'python3 is required' >&2; exit 1; }
python3 --version
