#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends git cmake ninja-build build-essential python3 python3-yaml curl jq pciutils
git clone https://github.com/ggml-org/llama.cpp.git /workspace/llama.cpp
git -C /workspace/llama.cpp checkout 84e66d03218711dfcaa993e5f64a546abe82df9a
cmake -S /workspace/llama.cpp -B /workspace/llama.cpp/build -G Ninja -DGGML_CUDA=ON -DGGML_NATIVE=OFF -DCMAKE_CUDA_ARCHITECTURES='75;86;89' -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=ON -DCMAKE_BUILD_TYPE=Release
cmake --build /workspace/llama.cpp/build --target llama-bench llama-server llama-cli llama-tokenize -j "$(nproc)"
mkdir -p /workspace/bin
find /workspace/llama.cpp/build/bin -maxdepth 1 -type f -perm -111 -exec ln -sf {} /workspace/bin/ \;
echo 'export PATH=/workspace/bin:$PATH' >> /etc/profile.d/vram-tier-map.sh
