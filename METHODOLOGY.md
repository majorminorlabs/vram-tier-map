# Methodology

## Version history

- **v1 (draft):** Initial policy for reproducible, comparable local inference
  measurements. Runtime commit pinned to `84e66d03218711dfcaa993e5f64a546abe82df9a`.
  Benchmark task inputs are committed as public data as of first publish.
- **v1.1:** Offer qualification uses the per-card CPU RAM floors and price
  caps in `harness/registry/cards.yaml`: RTX 3090 32 GB/$0.40 per hour, RTX
  3060 16 GB/$0.20, RTX 4060 Ti 16 GB/$0.35, and RTX 3070 16 GB/$0.20.
  RAM floors reflect typical owner systems; the per-config check decides fit.
  Reliability must be >=0.97; verified status is recorded but not required.
  Disk and native-width-specific PCIe bandwidth floors remain in force. Before each model/quant
  configuration, require free host RAM >= max(0, GGUF size - estimated VRAM
  fit) + 8 GiB; otherwise record `skipped: host RAM` and continue. Record host
  total RAM, the offer's raw `cpu_ram`, and free RAM at load in every result row.
  Record each host's verification status in run metadata and each result row.

- **v1.3:** Per-card provider routing, same image digest across providers, config-sensitive CPU-idle gate, and timestamped host exclusions.

## Pinned runtime image

The pinned llama.cpp commit is release tag `b11779`. The corresponding official
GHCR image tag (`full-cuda-b11779` / `full-cuda12-b11779`) was not available when
the runtime was checked, so the runtime image is built from that exact source
commit by GitHub Actions with CUDA Toolkit 12.2.2 for build and runtime. Its CUDA build sets
`CMAKE_CUDA_ARCHITECTURES="75;86;89"` and `GGML_NATIVE=OFF`; it includes
`llama-bench`, `llama-server`, `llama-cli`, and `llama-tokenize`. The published
image is `ghcr.io/majorminorlabs/vtm-llamacpp@sha256:cbaf8a54939ff5365785066196fbe9ff0f162eaf2022de4db15a8768a72107ed`, pulled by immutable digest and recorded in `harness/registry/runtime.yaml` and each run's `run.json`.
CUDA 12.2 requires Linux driver >=535.54.03 ([NVIDIA CUDA Toolkit release notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/)).

The CUDA `libcuda.so` stub is used only in the build stage to link the CUDA
binaries. Its build-stage `libcuda.so.1` symlink and linker search flags are not
copied into the runtime stage. The runtime stage does not add the stub to
`LD_LIBRARY_PATH` or the dynamic loader configuration, and no `cuda-compat`
directory appears on either loader path. The NVIDIA container runtime
supplies the real driver library to the running container.

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

Native PCIe specifications are versioned in `harness/registry/cards.yaml` for each card. Offer filtering uses Vast CLI offer fields `pci_gen` and `pcie_bw`: require generation 3 or newer and treat measured `pcie_bw` as a coarse floor of 8 for native x16 cards or 6 for native x8 cards. The Vast CLI exposes these fields in `search offers` results (see the [official CLI source](https://github.com/vast-ai/vast-cli/blob/master/vast.py)). `pcie_bw` is a marketplace measurement rather than a guaranteed theoretical link rate, so the card-run H2D transfer check is authoritative. It records the actual model load bytes and duration, UTC sample times, PCIe generation and width values, GPU utilization, and P-state while a pinned approximately 2.02 GB GGUF is loaded by llama-bench with `-ngl 99 -mmp 0`. Pass requires H2D >= 6 GB/s for native x16 cards or >= 5 GB/s for native x8 cards, with `width.max` and available `width.hostmax` equal to native width. PCIe generation under compute-only load does not determine eligibility. Smoke-mode PCIe, power, and RAM observations are warning-only. Card runs also require `power.limit >= 90%` of `power.default_limit`; both watt values and measured H2D GB/s are recorded in every result row.

## Provisioning and model storage

Offer qualification uses each card's `ram_floor_gb` and `vast_price_cap_usd_per_hour` in `harness/registry/cards.yaml`, reliability >=0.97, and does not require verified status. The same RAM floor is checked against raw Vast `cpu_ram` in the offer response; Vast reports this field in MB. Before each model/quant/configuration, estimate VRAM fit as the smaller of GGUF size and currently free GPU memory, then require free host RAM >= max(0, GGUF size - VRAM fit) + 8 GiB. If not, log `skipped: host RAM` and continue to the next configuration. Each result row records host total RAM, raw instance `cpu_ram`, free RAM at load, and host verification status; `run.json` records verification status per host. Before a card rental, run the offer funnel for that card only. Zero eligible offers stops that card before rental. One eligible host is allowed; record host replication as `unavailable` in `run.json`. The harness downloads and evaluates one model at a time, removes that model's GGUF before moving to the next, and records free disk before each download. It aborts before a download if available space is less than the GGUF size plus 10 GiB. Failed create responses are retried on the next eligible offer up to three offers per host attempt, after label/orphan cleanup; every create response and stderr are stored privately after token redaction. The private per-session spend ledger is checked against the $6 session ceiling before a rental, with smoke runs capped at 30 minutes and card runs at the six-hour hard limit.

Vast's `search offers` result schema exposes `cuda_max_good` and `driver_version`
([Vast CLI source](https://github.com/vast-ai/vast-cli/blob/master/vast.py)). The funnel
reports this CUDA compatibility filter after the PCIe floor; it accepts a sufficient
`cuda_max_good` or a `driver_version` meeting the image's documented minimum.

Instance startup is polled every 20 seconds. The private log records `actual_status`,
`status_msg`, and any image-pull progress. Eight minutes without a status or pull
progress update is a host startup stall; progressing pulls may continue for up to
25 minutes. A startup-stalled machine is destroyed, confirmed absent, excluded with
its reason in private notes, and retried within the shared five-host limit. Among
otherwise eligible offers, prefer `inet_down >= 200 Mbps` when its hourly price is
within $0.02 of the cheapest eligible offer.

## v1.2 (2026-10-23): smoke quality policy and PCIe traffic

Smoke mode treats PCIe, power-limit, and host-RAM checks as warn-only and logs the
observations. Its hard gates remain successful creation and transition to `running`, CUDA
offload evidence from the smoke benchmark, pulled results, normal teardown with zero
instances, and the independent watchdog destroying a SIGKILLed run within two minutes
with zero instances. The CUDA toolkit and minimum supported driver are pinned in
`runtime.yaml`; offer filtering uses Vast's `cuda_max_good` or `driver_version`, and
preflight records the actual driver and maximum CUDA version. A below-floor host is
rejected and replaced. A CUDA initialization error is a host failure, but two
consecutive hosts returning the same CUDA-init error stop the gate as an image problem.
Startup stalls and dead hosts are host failures; exclude them and advance to another
offer.

For card runs, the current power limit passes when it is at least 90% of the default power
limit. Record both watt values in every result row.

Card PCIe qualification measures host-to-device bandwidth by loading the pinned
approximately 2.02 GB preflight GGUF with `llama-bench -ngl 99 -mmp 0`. Record bytes,
load time, UTC sample timestamps, GPU utilization, P-state, current/max/host PCIe link
values, and measured H2D GB/s. Pass requires at least 6 GB/s for native x16 cards or
5 GB/s for the native x8 RTX 4060 Ti; `width.max` and available `width.hostmax` must match
native width. Every card result row records measured H2D GB/s and both power limits.
PCIe generation under compute-only load is not an acceptance criterion. Card runs can
reject up to five hosts; smoke host-quality warnings do not fail the smoke gate.

## v1.3 (2026-10-24): provider and config-sensitive CPU policy

Provider selection is versioned per card in `harness/registry/cards.yaml`: RTX 3090 uses
RunPod and the other registered cards use Vast. Both providers must use the same immutable
llama.cpp image digest recorded in `runtime.yaml` and each `run.json`. Results and run
metadata record provider, RunPod pod type (secure/community), and hourly rate. RunPod
control-plane auth is read through `runpodctl`'s saved configuration; credentials never
enter harness output or run artifacts. The RTX 3090 RunPod run uses community pods when
available within the card cap and the private session ledger enforces the $6 spend limit.

CPU idle is measured and recorded on every result. A host is required to have at least
90% CPU idle only for configurations that offload MoE layers to CPU; if that configuration
is busy, record `skipped: CPU busy` and proceed to other configurations. GPU-only configs
continue regardless of idle percentage.

Host exclusions are stored with a reason and UTC timestamp. Startup stalls, SSH/image
timeouts, CPU busy, and another GPU process expire after 24 hours. CUDA initialization,
PCIe qualification, power-cap, and disk-full failures remain permanent exclusions. The
historical permanent machine list is retained; timestamped entries are kept in the private
exclusion ledger.
