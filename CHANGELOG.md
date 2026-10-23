# Changelog

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
