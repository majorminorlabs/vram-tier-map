#!/usr/bin/env python3
"""Rebuild generated CSVs from append-only card run folders."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULT_FIELDS = ["card_id", "run_id", "model_id", "quant", "task_pass", "prompt_tps", "generation_tps", "ttft_ms", "vram_peak_gb", "cost_usd", "flags"]
PICK_FIELDS = ["card_id", "tier", "pick_model_id", "pick_quant", "runner_up_model_id", "runner_up_quant", "generation_tps", "ttft_ms", "flags"]


def main():
    results = []
    tiers = {}
    for csv_path in sorted(ROOT.glob("cards/*/runs/*/results.csv")):
        card_id = csv_path.parents[2].name
        meta_path = csv_path.parent / "run.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        tiers[card_id] = meta.get("tier", tiers.get(card_id, ""))
        with csv_path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                row.setdefault("card_id", card_id)
                row.setdefault("run_id", csv_path.parent.name)
                results.append({key: row.get(key, "") for key in RESULT_FIELDS})

    write_csv(ROOT / "data/all_results.csv", RESULT_FIELDS, results)
    grouped = {}
    for row in results:
        if row["task_pass"].lower() not in ("true", "1", "yes"):
            continue
        try:
            tps = float(row["generation_tps"])
            ttft = float(row["ttft_ms"])
        except (TypeError, ValueError):
            continue
        if not row["model_id"] or not row["quant"]:
            continue
        grouped.setdefault((row["card_id"], row["run_id"]), []).append((tps, ttft, row))

    picks = []
    cards_with_results = {card_id for card_id, _run_id in grouped}
    for card_id in sorted(set(tiers) | cards_with_results):
        latest_run = max((run_id for card, run_id in grouped if card == card_id), default="")
        ranked = sorted(grouped.get((card_id, latest_run), []), key=lambda item: (-item[0], item[1]))
        top = ranked[0][2] if ranked else {}
        second = ranked[1][2] if len(ranked) > 1 else {}
        picks.append({
            "card_id": card_id, "tier": tiers.get(card_id, ""),
            "pick_model_id": top.get("model_id", ""), "pick_quant": top.get("quant", ""),
            "runner_up_model_id": second.get("model_id", ""), "runner_up_quant": second.get("quant", ""),
            "generation_tps": top.get("generation_tps", ""), "ttft_ms": top.get("ttft_ms", ""),
            "flags": top.get("flags", "")
        })
    write_csv(ROOT / "data/picks_latest.csv", PICK_FIELDS, picks)
    update_readme(picks)


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def update_readme(picks):
    path = ROOT / "README.md"
    content = path.read_text(encoding="utf-8")
    start, end = "<!-- PICKS:START -->", "<!-- PICKS:END -->"
    before, rest = content.split(start, 1)
    _old, after = rest.split(end, 1)
    populated = [row for row in picks if row["pick_model_id"]]
    if populated:
        lines = ["| Card | Tier | Pick | Runner-up | Generation tok/s | TTFT ms | Flags |", "|---|---|---|---|---:|---:|---|"]
        for row in populated:
            pick = f"{row['pick_model_id']} ({row['pick_quant']})"
            runner = f"{row['runner_up_model_id']} ({row['runner_up_quant']})" if row["runner_up_model_id"] else "—"
            lines.append(f"| {row['card_id']} | {row['tier']} | {pick} | {runner} | {row['generation_tps']} | {row['ttft_ms']} | {row['flags']} |")
        table = "\n".join(lines)
    else:
        table = "No benchmark runs have been published yet."
    path.write_text(before + start + "\n" + table + "\n" + end + after, encoding="utf-8")


if __name__ == "__main__":
    main()
