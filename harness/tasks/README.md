# Benchmark tasks

`cuda-recipes-001-tasks.json` vendors the complete 40-task input set: 12 coding,
8 terminal/system, 8 direct tool-use, 6 structured-data, and 6 multi-step tasks.
`cuda-recipes-001-scoring.json` and `scorer/` contain the scoring contract,
allowlisted simulated tools, and strict deterministic reference checks. Each
task includes its source commit, attribution, license, and third-party source
list. Direct tool-use and multi-step tasks (14 total) have exact ordered tool
trace pass/fail checks and matching final-response checks. Each run must
identify the exact task-set version it used.
