#!/usr/bin/env bash
set -euo pipefail
test "$(git -C /workspace/llama.cpp rev-parse HEAD)" = 84e66d03218711dfcaa993e5f64a546abe82df9a
test -x /workspace/bin/llama-bench
test -x /workspace/llama.cpp/build/bin/llama-server
mkdir -p /workspace/output /workspace/model-cache
