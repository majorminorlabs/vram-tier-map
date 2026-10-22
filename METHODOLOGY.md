# Methodology

## Version history

- **v1 (draft):** Initial policy for reproducible, comparable local inference
  measurements. Finalize before publishing an edition.

## v1 policy (draft)

Each result belongs to one card, run, model, quantization, and execution
configuration. Preserve raw logs and host details with the run. Compare only
results with the same task set, context length, runtime settings, and metric
definitions; disclose deviations as flags.

The current pick is the best eligible model/quant for the card under the
published task suite, context policy, and price cap. A result is eligible only
when all required tasks pass and measurements are complete. Rank eligible
results by aggregate generation throughput (tokens/second); use lower
time-to-first-token (TTFT) as the tie-breaker. A missing runner-up is blank.
Any failures, missing measurements, hardware/runtime deviations, or price cap
exceptions must be surfaced in flags and must not be silently discarded.

Record prompt processing throughput, generation throughput, TTFT, memory use,
task pass/fail, and rental cost when measured. Units, aggregation windows,
warm-up policy, concurrency, context length, and task definitions must be
fixed in the harness and recorded in each run before an edition is published.

Never edit a committed run. Corrected or repeated measurements require a new
run ID; aggregate outputs and picks are regenerated from the run folders.

## Context policy

The context length and workload must be pinned in each task/config. Do not
compare results across different context lengths as if they were equivalent.
Document any model-specific prompt limits or truncation in the result flags.
