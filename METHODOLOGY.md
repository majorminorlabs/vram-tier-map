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
