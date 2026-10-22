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

## 2026-10-21

- Methodology v1 draft now permits committing benchmark task inputs. Vendor
  CUDA Recipes 001's 40 tasks and strict deterministic scorer, with source
  attribution and CC-BY-4.0 licensing; continue excluding agent prompts, plans,
  handoffs, social assets, and articles.
