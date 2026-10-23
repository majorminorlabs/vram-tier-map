#!/usr/bin/env python3
"""Rebuild generated CSVs from append-only card run folders."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_FIELDS = ["card_id", "run_id", "model_id", "quant", "task_set_version", "fit_max_ctx", "kv_type", "n_cpu_moe", "peak_vram_gb", "peak_ram_gb", "prompt_tps_64k", "decode_tps_64k", "decode_tps_120k", "ttft_64k_ms", "strict_pass_rate", "task_wall_clock_s", "speed_cv_percent", "cost_usd", "pcie_gen_actual", "pcie_width_actual", "flags"]
PICK_FIELDS = ["card_id", "tier", "pick_model_id", "pick_quant", "runner_up_model_id", "runner_up_quant", "strict_pass_rate", "decode_tps_64k", "ttft_64k_ms", "flags"]


def main():
    results = []
    cards = json.loads((ROOT / "harness/registry/cards.yaml").read_text(encoding="utf-8"))["cards"]
    tiers = {card["card_id"]: card.get("tier", "") for card in cards}
    latest_runs = {}
    for csv_path in sorted(ROOT.glob("cards/*/runs/*/results.csv")):
        card_id = csv_path.parents[2].name
        meta_path = csv_path.parent / "run.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        tiers[card_id] = meta.get("tier", tiers.get(card_id, ""))
        latest_runs[card_id] = max(csv_path.parent.name, latest_runs.get(card_id, ""))
        with csv_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                row.setdefault("card_id", card_id)
                row.setdefault("run_id", csv_path.parent.name)
                results.append({key: row.get(key, "") for key in RESULT_FIELDS})

    write_csv(ROOT / "data/all_results.csv", RESULT_FIELDS, results)
    grouped = {}
    for row in results:
        try:
            rate = float(row["strict_pass_rate"])
            tps = float(row["decode_tps_64k"])
            ttft = float(row["ttft_64k_ms"])
            task_time = float(row["task_wall_clock_s"])
        except (TypeError, ValueError):
            continue
        if not row["model_id"] or not row["quant"] or ttft > 180_000 or tps < 15:
            continue
        grouped.setdefault((row["card_id"], row["run_id"]), []).append((rate, task_time, tps, ttft, row))

    picks = []
    cards_with_results = {row["card_id"] for row in results}
    for card_id in sorted(set(tiers) | cards_with_results):
        latest_run = latest_runs.get(card_id, "")
        ranked = rank_candidates(grouped.get((card_id, latest_run), []))
        top = ranked[0] if ranked else {}
        second = ranked[1] if len(ranked) > 1 else {}
        picks.append({
            "card_id": card_id, "tier": tiers.get(card_id, ""),
            "pick_model_id": top.get("model_id", ""), "pick_quant": top.get("quant", ""),
            "runner_up_model_id": second.get("model_id", ""), "runner_up_quant": second.get("quant", ""),
            "strict_pass_rate": top.get("strict_pass_rate", ""), "decode_tps_64k": top.get("decode_tps_64k", ""), "ttft_64k_ms": top.get("ttft_64k_ms", ""),
            "flags": top.get("flags", "")
        })
    write_csv(ROOT / "data/picks_latest.csv", PICK_FIELDS, picks)
    update_readme(picks)
    update_card_readmes(results, tiers)


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def rank_candidates(candidates):
    """Repeatedly apply the v1 pass-rate rule, then completion-time tiebreak."""
    remaining = list(candidates)
    ranked = []
    while remaining:
        best_rate = max(row[0] for row in remaining)
        within_five_points = [row for row in remaining if best_rate - row[0] <= 0.05]
        winner = min(within_five_points, key=lambda row: (row[1], -row[0], -row[2]))
        ranked.append(winner[4])
        remaining.remove(winner)
    return ranked


def update_readme(picks):
    path = ROOT / "README.md"
    content = path.read_text(encoding="utf-8")
    start, end = "<!-- PICKS:START -->", "<!-- PICKS:END -->"
    before, rest = content.split(start, 1)
    _old, after = rest.split(end, 1)
    populated = [row for row in picks if row["pick_model_id"]]
    if populated:
        lines = ["| Card | Tier | Pick | Runner-up | Strict pass rate | Decode tok/s at 64K | TTFT ms at 64K | Flags |", "|---|---|---|---|---:|---:|---:|---|"]
        for row in populated:
            pick = f"{row['pick_model_id']} ({row['pick_quant']})"
            runner = f"{row['runner_up_model_id']} ({row['runner_up_quant']})" if row["runner_up_model_id"] else "—"
            lines.append(f"| {row['card_id']} | {row['tier']} | {pick} | {runner} | {row['strict_pass_rate']} | {row['decode_tps_64k']} | {row['ttft_64k_ms']} | {row['flags']} |")
        table = "\n".join(lines)
    else:
        table = "No benchmark runs have been published yet."
    path.write_text(before + start + "\n" + table + "\n" + end + after, encoding="utf-8")


def update_card_readmes(results, tiers):
    by_card_run = {}
    for row in results:
        by_card_run.setdefault((row["card_id"], row["run_id"]), []).append(row)
    cards = json.loads((ROOT / "harness/registry/cards.yaml").read_text(encoding="utf-8"))["cards"]
    for card in cards:
        card_id = card["card_id"]
        path = ROOT / "cards" / card_id / "README.md"
        if not path.exists():
            continue
        runs = sorted((run_id for c, run_id in by_card_run if c == card_id))
        lines = ["## Run history", "", "| Run ID | Pick | Runner-up | Pass rate | TTFT at 64K (ms) | Decode tok/s at 64K | Flags |", "|---|---|---|---:|---:|---:|---|"]
        if not runs:
            lines.append("| — | No runs yet | — | — | — | — | — |")
        for run_id in runs:
            ranked = rank_candidates([candidate_tuple(r) for r in by_card_run[(card_id, run_id)] if is_usable(r)])
            first = ranked[0] if ranked else {}
            second = ranked[1] if len(ranked) > 1 else {}
            pick = f"{first.get('model_id','')} ({first.get('quant','')})" if first else "No usable configuration"
            runner = f"{second.get('model_id','')} ({second.get('quant','')})" if second else "—"
            lines.append(f"| {run_id} | {pick} | {runner} | {first.get('strict_pass_rate','')} | {first.get('ttft_64k_ms','')} | {first.get('decode_tps_64k','')} | {first.get('flags','')} |")
        contents = path.read_text(encoding="utf-8")
        start, end = "<!-- RUN_HISTORY:START -->", "<!-- RUN_HISTORY:END -->"
        if start not in contents or end not in contents:
            continue
        before, rest = contents.split(start, 1)
        _old, after = rest.split(end, 1)
        path.write_text(before + start + "\n" + "\n".join(lines) + "\n" + end + after, encoding="utf-8")


def is_usable(row):
    try:
        return float(row["ttft_64k_ms"]) <= 180_000 and float(row["decode_tps_64k"]) >= 15 and row["model_id"] and row["quant"]
    except (TypeError, ValueError):
        return False


def candidate_tuple(row):
    return (float(row["strict_pass_rate"]), float(row["task_wall_clock_s"]), float(row["decode_tps_64k"]), float(row["ttft_64k_ms"]), row)


if __name__ == "__main__":
    main()
