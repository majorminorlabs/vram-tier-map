FROM nvidia/cuda:12.8.1-devel-ubuntu22.04@sha256:a99a1860ba8e2916e5c3e73b72ec4c4301653a84586e05bfc9a2aa2d58027e97 AS build

ARG LLAMA_CPP_COMMIT=84e66d03218711dfcaa993e5f64a546abe82df9a
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ninja-build libssl-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# CUDA development images include the driver stub without the .so.1 name
# required by downstream binaries at link time. This is only a link-time
# dependency; the NVIDIA container runtime provides the real driver library.
RUN ln -s /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1

RUN git init /opt/llama.cpp \
    && git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp.git \
    && git -C /opt/llama.cpp fetch --depth=1 origin "${LLAMA_CPP_COMMIT}" \
    && git -C /opt/llama.cpp checkout --detach FETCH_HEAD \
    && test "$(git -C /opt/llama.cpp rev-parse HEAD)" = "${LLAMA_CPP_COMMIT}"

RUN cmake -S /opt/llama.cpp -B /build -G Ninja \
    -DGGML_CUDA=ON \
    -DGGML_NATIVE=OFF \
    -DCMAKE_CUDA_ARCHITECTURES="75;86;89" \
    -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
    -DCMAKE_SHARED_LINKER_FLAGS="-L/usr/local/cuda/lib64/stubs -Wl,-rpath-link,/usr/local/cuda/lib64/stubs" \
    -DLLAMA_BUILD_TESTS=OFF \
    -DLLAMA_BUILD_EXAMPLES=ON \
    -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /build --target llama-bench llama-server llama-cli llama-tokenize -j "$(nproc)"

FROM nvidia/cuda:12.8.1-runtime-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git jq libgomp1 pciutils procps python3 python3-yaml \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /workspace/llama.cpp/build/bin

COPY --from=build /opt/llama.cpp/ /workspace/llama.cpp/
COPY --from=build /build/bin/ /workspace/llama.cpp/build/bin/
RUN mkdir -p /workspace/bin \
    && ln -s /workspace/llama.cpp/build/bin/llama-bench /workspace/bin/llama-bench \
    && ln -s /workspace/llama.cpp/build/bin/llama-server /workspace/bin/llama-server \
    && ln -s /workspace/llama.cpp/build/bin/llama-cli /workspace/bin/llama-cli \
    && ln -s /workspace/llama.cpp/build/bin/llama-tokenize /workspace/bin/llama-tokenize \
    && ln -s /workspace/llama.cpp/build/bin/llama-bench /usr/local/bin/llama-bench \
    && ln -s /workspace/llama.cpp/build/bin/llama-server /usr/local/bin/llama-server

WORKDIR /workspace
CMD ["bash", "-lc", "sleep infinity"]
