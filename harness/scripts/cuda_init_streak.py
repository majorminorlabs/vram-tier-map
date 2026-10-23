#!/usr/bin/env python3
"""Persist consecutive identical CUDA-init errors across smoke host retries."""
import json, re, sys
from pathlib import Path


def extract_error(log_text):
    for line in log_text.splitlines():
        match = re.search(r"failed to initialize CUDA:\s*(.+?)\s*$", line)
        if match:
            return " ".join(match.group(1).split())
    return None


def record(path, error):
    path = Path(path)
    try:
        previous = json.loads(path.read_text())
    except (OSError, ValueError):
        previous = {}
    count = int(previous.get("consecutive_count", 0)) + 1 if previous.get("error") == error else 1
    payload = {"error": error, "consecutive_count": count}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: cuda_init_streak.py STATE_FILE LOG_FILE")
    error = extract_error(Path(sys.argv[2]).read_text(errors="replace"))
    if error is None:
        raise SystemExit(1)
    print(json.dumps(record(sys.argv[1], error)))
