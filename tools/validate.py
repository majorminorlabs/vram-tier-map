#!/usr/bin/env python3
"""Validate run folders and results.csv against the versioned schema."""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["card_id", "run_id", "model_id", "quant", "task_pass", "prompt_tps", "generation_tps", "ttft_ms", "vram_peak_gb", "cost_usd", "flags"]
RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_r\d{2,}$")
NUMERIC = {"prompt_tps", "generation_tps", "ttft_ms", "vram_peak_gb", "cost_usd"}


def main():
    failures = []
    runs = sorted(ROOT.glob("cards/*/runs/*"))
    for folder in runs:
        if not folder.is_dir():
            continue
        card = folder.parents[1].name
        run_id = folder.name
        if not RUN_ID.fullmatch(run_id):
            failures.append(f"{folder}: invalid run_id")
        for required in ("run.json", "results.csv", "configs", "hosts", "logs", "tasks"):
            if not (folder / required).exists():
                failures.append(f"{folder}: missing {required}")
        try:
            meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
            for key in ("llama_cpp_commit", "image_digest", "harness_git_sha", "schema_version", "methodology_version", "hosts_used", "wall_clock", "cost_usd"):
                if key not in meta:
                    failures.append(f"{folder}/run.json: missing {key}")
            if meta.get("schema_version") != "1.0":
                failures.append(f"{folder}/run.json: schema_version must be 1.0")
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{folder}/run.json: {exc}")
            continue
        try:
            with (folder / "results.csv").open(newline="", encoding="utf-8") as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != FIELDS:
                    failures.append(f"{folder}/results.csv: expected columns {FIELDS}")
                for index, row in enumerate(reader, start=2):
                    if row.get("card_id") != card or row.get("run_id") != run_id:
                        failures.append(f"{folder}/results.csv:{index}: card_id/run_id mismatch")
                    for key in ("model_id", "quant", "task_pass", "generation_tps", "ttft_ms", "flags"):
                        if row.get(key, "") == "":
                            failures.append(f"{folder}/results.csv:{index}: missing {key}")
                    if row.get("task_pass", "").lower() not in ("true", "false"):
                        failures.append(f"{folder}/results.csv:{index}: task_pass must be true or false")
                    for key in NUMERIC:
                        value = row.get(key, "")
                        if value:
                            try:
                                if float(value) < 0:
                                    raise ValueError()
                            except ValueError:
                                failures.append(f"{folder}/results.csv:{index}: {key} must be nonnegative or blank")
        except OSError as exc:
            failures.append(f"{folder}/results.csv: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Validated {len(runs)} run folder(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
