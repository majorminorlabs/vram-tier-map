# Changelog

## 2026-10-23

- Rebuilt the pinned llama.cpp image with CUDA 12.2.2 on GitHub Actions in 31 minutes 10 seconds.
  Public immutable digest: `sha256:cbaf8a54939ff5365785066196fbe9ff0f162eaf2022de4db15a8768a72107ed`.
  Anonymous linux/amd64 pull succeeded. Compressed layers total 1,701,830,331 bytes;
  Docker reports 1,701,847,670 bytes uncompressed. Verified no CUDA stub files or
  stub link on the loader path, and no cuda-compat path on the loader path.

- Switched the pinned llama.cpp image build to CUDA 12.2.2 build and runtime stages,
  preserving the `75;86;89` architecture list and driver-stub build-only setup.
  CUDA compatibility filtering checks Vast's `cuda_max_good` and `driver_version`
  offer fields (listed in the [Vast CLI source](https://github.com/vast-ai/vast-cli/blob/master/vast.py));
  preflight records both the NVIDIA driver and reported maximum CUDA version.
  Two consecutive identical CUDA initialization failures now stop smoke as an
  image issue. Result schema 1.5 records driver and CUDA versions per row and in
  `run.json`.
- PCIe transfer samples retain GPU utilization and P-state at each sample; mocked
  preflight coverage asserts both values are populated.

- Rebuilt the public CUDA image on GitHub Actions in 43 minutes 45 seconds after
  the smoke gate found missing runtime `libgomp.so.1`. New digest:
  `sha256:6b1018ad94124b8f13c9216da98332df41051c2d73ef782a9744836b09033d3c`.
  Anonymous GHCR pull succeeded. Verified `libgomp` resolves and the CUDA driver
  stub is absent from the runtime library path, loader configuration, and final
  image. The image remains slim at 2.231 GiB compressed (about 3.632 GiB
  uncompressed).

- Methodology v1.2 makes smoke PCIe, power, and RAM observations warn-only while retaining
  its lifecycle, CUDA offload, result-pull, teardown, and kill-9 watchdog gates.
- Card power acceptance is `power.limit >= 90% * power.default_limit`; both watt values
  are included in result rows.
- Replaced compute-only PCIe acceptance with measured host-to-device loading bandwidth
  (6 GB/s x16, 5 GB/s x8), native-width capability checks, and timestamped utilization,
  P-state, power, and PCIe samples. Card attempts may reject up to five hosts.

- Startup now polls every 20 seconds, logs state/message/pull progress privately,
  marks eight-minute stalls as host failures, and permits image pulls up to 25
  minutes. Failed startup hosts are excluded and retried within the shared
  three-host limit. Near-price offers prefer `inet_down>=200` when within $0.02/h.
- Pinned image layers measure 2.230 GiB compressed and an estimated 3.631 GiB
  uncompressed; the existing CUDA runtime stage is already slim.
- Methodology v1.1 sets per-card CPU RAM floors and price caps, makes host
  verification informational, and enforces a per-configuration free-RAM
  requirement with an 8 GiB reserve. Result rows and run metadata include host
  total RAM, raw offer CPU RAM, free RAM at load, and verification status.
- Card runs now gate on that card's offer funnel only. A single eligible host is
  allowed and is recorded as having replication `unavailable`.
- Preflight now records idle/max/host-max PCIe capability and samples negotiated
  generation and width during a 10-second GPU benchmark load. Idle power-saving
  downshift no longer rejects a suitable host; results schema is 1.3.
- Smoke rentals now select only registered cards. Removed three private
  smoke-session host exclusions after confirming they were idle Gen 1 readings.
- Offer filters now use reliability >= 0.97 and measured pcie_bw floors of 8
  (native x16) or 6 (native x8); smoke searches use a $0.30/h cap. A
  no_such_ask create response triggers immediate orphan cleanup and re-search.

Not yet published. Add dated entries for methodology, schema, harness, and
edition changes. Do not rewrite historical run records.

## 2026-10-22

- Pinned the published GHCR CUDA image by digest. GitHub Actions image build
  completed successfully in 39 minutes 6 seconds (2026-10-22 16:57:42–17:36:48
  UTC), below the five-hour optimization threshold.
- Clarify that the CUDA driver stub is build-stage only and define GPU evidence
  required for smoke-gate and first Card 1 benchmark acceptance.
- Record per-benchmark CUDA device/offload logs and concurrent `nvidia-smi`
  memory samples; reject CPU-only or partial-layer runs.
- Route all teardown through non-interactive `--yes` destroys with instance-list
  polling and retries; verify uploaded script hashes and executable bits over
  SSH/SCP, and cover teardown entry points with a mocked Vast CLI test.

## 2026-10-21

- Methodology v1 draft now permits committing benchmark task inputs. Vendor
  CUDA Recipes 001's 40 tasks and strict deterministic scorer, with source
  attribution and CC-BY-4.0 licensing; continue excluding agent prompts, plans,
  handoffs, social assets, and articles.

# 2026-10-22

- Added per-card native PCIe generation and width, offer filtering using Vast's `pci_gen` and `pcie_bw`, actual negotiated PCIe values in result rows/run metadata, and a flag for Gen 3 links on Gen 4 cards.
- Preflight-rejected hosts are destroyed, confirmed absent, excluded, and replaced up to a three-host limit; execution failures remain gate failures.
- Watchdog treats whitespace/empty instance output and an empty list as zero instances and exits once its run's instances are gone. Mock coverage added.

# 2026-10-23

- Create diagnostics now retain redacted stderr and explicit HTTP status summaries; create requires JSON `success: true` and `new_contract`, then retries up to three disk-qualified offers with cleanup and per-run offer skips.
- Added per-card scratch disk requirements, offer-side disk filtering, one-model-at-a-time GGUF deletion, and free-space checks with a 10 GiB reserve.
- Added no-rental create retry, empty stdout, and disk filtering tests. Added a private estimated spend ledger and pre-rental $6 session cap.
- Smoke-only offer qualification includes the verified RTX A2000 PCIe 4.0 x16 profile so the smoke search can use eligible GPUs outside the production card registry.
- PCIe offer filtering uses a coarse `pcie_bw>=7.88` floor; preflight remains authoritative for exact lane width. Added the smoke-only Quadro RTX 4000 PCIe 3.0 x16 profile.
- **v1.3:** Added per-card provider routing (RunPod for RTX 3090; Vast for other cards) with the same pinned image digest. Run metadata and each row identify provider, pod type, and hourly rate. CPU idle is a hard gate only for CPU-offloaded configurations. Added timestamped temporary/permanent host exclusion policy.
