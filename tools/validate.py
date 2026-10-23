#!/usr/bin/env python3
"""Validate run folders and results.csv against the versioned schema."""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["card_id", "run_id", "model_id", "quant", "task_set_version", "fit_max_ctx", "kv_type", "n_cpu_moe", "peak_vram_gb", "peak_ram_gb", "prompt_tps_64k", "decode_tps_64k", "decode_tps_120k", "ttft_64k_ms", "strict_pass_rate", "task_wall_clock_s", "speed_cv_percent", "cost_usd", "pcie_gen_actual", "pcie_width_actual", "pcie_gen_idle", "pcie_gen_max", "pcie_gen_hostmax", "pcie_width_idle", "pcie_width_max", "pcie_width_hostmax", "power_limit_w", "power_default_limit_w", "pcie_h2d_gbps", "driver_version", "host_cuda_max", "host_ram_total_gb", "instance_cpu_ram", "free_ram_at_load_gb", "verified_status", "flags"]
RUN_ID = re.compile(r"^\d{4}-\d{2}-\d{2}_r\d{2,}$")
NUMERIC = {"fit_max_ctx", "n_cpu_moe", "peak_vram_gb", "peak_ram_gb", "prompt_tps_64k", "decode_tps_64k", "decode_tps_120k", "ttft_64k_ms", "strict_pass_rate", "task_wall_clock_s", "speed_cv_percent", "cost_usd", "pcie_gen_actual", "pcie_width_actual", "pcie_gen_idle", "pcie_gen_max", "pcie_gen_hostmax", "pcie_width_idle", "pcie_width_max", "pcie_width_hostmax", "power_limit_w", "power_default_limit_w", "pcie_h2d_gbps", "host_ram_total_gb", "instance_cpu_ram", "free_ram_at_load_gb"}


def main():
    failures = validate_task_pack()
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
            for key in ("llama_cpp_commit", "image_digest", "cuda_toolkit", "minimum_driver_version", "harness_git_sha", "schema_version", "methodology_version", "hosts_used", "wall_clock", "cost_usd"):
                if key not in meta:
                    failures.append(f"{folder}/run.json: missing {key}")
            if meta.get("schema_version") != "1.5":
                failures.append(f"{folder}/run.json: schema_version must be 1.5")
            if meta.get("methodology_version") != "v1.1":
                failures.append(f"{folder}/run.json: methodology_version must be v1.1")
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
                    for key in ("model_id", "quant", "task_set_version", "kv_type", "flags"):
                        if row.get(key, "") == "":
                            failures.append(f"{folder}/results.csv:{index}: missing {key}")
                    for key in ("host_ram_total_gb", "instance_cpu_ram", "free_ram_at_load_gb", "verified_status", "driver_version", "host_cuda_max"):
                        if row.get(key, "") == "":
                            failures.append(f"{folder}/results.csv:{index}: missing {key}")
                    for key in NUMERIC:
                        value = row.get(key, "")
                        if value:
                            try:
                                parsed = float(value)
                                if parsed < 0 or (key == "strict_pass_rate" and parsed > 1):
                                    raise ValueError()
                            except ValueError:
                                rule = "between 0 and 1" if key == "strict_pass_rate" else "nonnegative"
                                failures.append(f"{folder}/results.csv:{index}: {key} must be {rule} or blank")
        except OSError as exc:
            failures.append(f"{folder}/results.csv: {exc}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Validated {len(runs)} run folder(s).")
    return 0


def validate_task_pack():
    failures = []
    task_path = ROOT / "harness/tasks/cuda-recipes-001-tasks.json"
    try:
        tasks = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{task_path}: {exc}"]
    if len(tasks) != 40:
        failures.append(f"{task_path}: expected 40 tasks, got {len(tasks)}")
    ids = [task.get("id") for task in tasks]
    if len(ids) != len(set(ids)):
        failures.append(f"{task_path}: task IDs are not unique")
    for task in tasks:
        attribution = task.get("attribution", {})
        for field in ("creator", "source", "source_file", "source_commit", "license", "license_url", "third_party_sources"):
            if field not in attribution:
                failures.append(f"{task_path}: {task.get('id')} missing attribution.{field}")
        if task.get("expected_calls") is not None and task.get("scorer") not in (
            "canonical-json-equality-v1",
            "ordered-simulated-state-transition-v1",
        ):
            failures.append(f"{task_path}: {task.get('id')} tool-call scorer is not the pinned strict scorer")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
