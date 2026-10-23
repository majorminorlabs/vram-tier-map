# Methodology

## Version history

- **v1 (draft):** Initial policy for reproducible, comparable local inference
  measurements. Runtime commit pinned to `84e66d03218711dfcaa993e5f64a546abe82df9a`.
  Benchmark task inputs are committed as public data as of first publish.

## Pinned runtime image

The pinned llama.cpp commit is release tag `b11779`. The corresponding official
GHCR image tag (`full-cuda-b11779` / `full-cuda12-b11779`) was not available when
the runtime was checked, so the runtime image is built from that exact source
commit by GitHub Actions. Its CUDA build sets
`CMAKE_CUDA_ARCHITECTURES="75;86;89"` and `GGML_NATIVE=OFF`; it includes
`llama-bench`, `llama-server`, `llama-cli`, and `llama-tokenize`. The published
image is pulled by immutable digest. The digest is recorded in
`harness/registry/runtime.yaml` and each run's `run.json`.
Published image: `ghcr.io/majorminorlabs/vtm-llamacpp@sha256:432a22a393d71275c62b0aa3a1e3261e0b8b464830807e11b3df2bba114c5a90`.

The CUDA `libcuda.so` stub is used only in the build stage to link the CUDA
binaries. Its build-stage `libcuda.so.1` symlink and linker search flags are not
copied into the runtime stage. The runtime stage does not add the stub to
`LD_LIBRARY_PATH` or the dynamic loader configuration; the NVIDIA container
runtime supplies the real driver library to the running container.

## GPU execution gate

A smoke benchmark passes only when its preserved `llama-bench` log identifies
the same CUDA device name reported by `nvidia-smi`, reports every model layer
offloaded to the GPU with a non-zero layer count, and `nvidia-smi` samples show
GPU memory use rising by at least 32 MiB while the benchmark runs. Exit code 0
alone is insufficient. Apply the same evidence check to the first benchmark
configuration on Card 1 before treating Card 1 results as valid. The harness
preserves the benchmark log and concurrent GPU-memory samples in the run output.

## v1 policy (draft)

The CUDA Recipes 001 task suite is committed under `harness/tasks/` and licensed
CC-BY-4.0, with source commit and attribution recorded per task. It contains 40
deterministic tasks across coding, terminal/system, tool use, structured data,
and multi-step execution. Tool tasks use simulated fixtures and strict ordered
call trace plus final-response checks; no model judge is used. These benchmark
inputs are public as of first publish. Agent instructions, plans, handoffs,
social assets, and articles remain outside the repository.

Each result belongs to one card, run, model, quantization, and execution
configuration. Preserve raw logs and host details with the run. Compare only
results with the same task set, context length, runtime settings, and metric
definitions; disclose deviations as flags.

The current pick is the best eligible model/quant for the card under the
published task suite, context policy, and price cap. For the draft v1 pick,
usable means TTFT at 64K is at most 180 seconds and decode is at least 15
tokens/second at approximately 64K depth. Among usable configurations, highest
strict task pass rate wins; within five percentage points, lower wall-clock
time to completion wins. The runner-up is the next usable configuration under
the same ordering. A missing runner-up is blank.
Any failures, missing measurements, hardware/runtime deviations, or price cap
exceptions must be surfaced in flags and must not be silently discarded.

Record prompt processing throughput, generation throughput, TTFT, memory use,
task pass/fail, and rental cost when measured. Units, aggregation windows,
warm-up policy, concurrency, context length, and task definitions must be
fixed in the harness and recorded in each run before an edition is published.

Never edit a committed run. Corrected or repeated measurements require a new
run ID; aggregate outputs and picks are regenerated from the run folders.

## Context policy

The context length and workload must be pinned in each task/config. The target
context is 131,072 tokens, with 65,536 as a fallback after KV-cache and CPU-MoE
fit attempts. Use llama-bench `--n-depth` to prefill the requested synthetic
depth; the task-suite long-context prefix is a separately measured real prompt.
Do not
compare results across different context lengths as if they were equivalent.
Document any model-specific prompt limits or truncation in the result flags.

## PCIe host qualification

Native PCIe specifications are versioned in `harness/registry/cards.yaml` for each card. Offer filtering uses Vast CLI offer fields `pci_gen` and `pcie_bw`: require generation 3 or newer and bandwidth sufficient for the card's native lane width (15.75 GB/s for x16, 7.88 GB/s for x8). The Vast CLI exposes these fields in `search offers` results (see the [official CLI source](https://github.com/vast-ai/vast-cli/blob/master/vast.py)). Preflight requires negotiated link width equal to the card's native width and current PCIe generation at least 3. Each model result records actual generation and width. A Gen 3 link on a Gen 4 card is flagged in result rows and run metadata; it remains eligible if it meets the width and minimum-generation rules.
