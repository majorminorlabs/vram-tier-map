#!/usr/bin/env python3
"""Watch Vast instance startup and classify a stalled host without dumping raw API data."""
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STALL_SECONDS = 8 * 60
TOTAL_SECONDS = 25 * 60
POLL_SECONDS = 20
TOKEN_LIKE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_./+=-]{32,}(?![A-Za-z0-9])")
PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s*%")


def redact(value):
    return TOKEN_LIKE.sub("[redacted]", str(value or ""))


def unwrap(raw):
    value = raw if isinstance(raw, dict) else {}
    if isinstance(value, list):
        value = value[0] if value else {}
    if isinstance(value, dict) and isinstance(value.get("instances"), list):
        value = value["instances"][0] if value["instances"] else {}
    return value if isinstance(value, dict) else {}


def status_fields(raw):
    item = unwrap(raw)
    status = item.get("actual_status", item.get("status", "unknown"))
    message = item.get("status_msg", item.get("status_message", ""))
    progress_values = []

    def visit(value, path=""):
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                child_path = f"{path}.{key}" if path else str(key)
                if "progress" in lowered and ("image" in lowered or "pull" in lowered):
                    progress_values.append(f"{child_path}={child}")
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(item)
    if not progress_values:
        match = PERCENT.search(str(message))
        if match:
            progress_values.append(match.group(0).replace(" ", ""))
    return str(status), str(message or ""), ";".join(progress_values)


def fetch_vast(instance_id):
    result = subprocess.run(
        ["vastai", "show", "instance", str(instance_id), "--raw"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"vastai show instance exited {result.returncode}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("vastai show instance returned invalid JSON") from exc


def monitor_startup(instance_id, log_path, *, fetch=fetch_vast, sleep=time.sleep,
                    clock=time.monotonic, poll_seconds=POLL_SECONDS,
                    stall_seconds=STALL_SECONDS, total_seconds=TOTAL_SECONDS):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = previous_activity = clock()
    previous = None
    while True:
        query_error = ""
        try:
            fields = status_fields(fetch(instance_id))
        except Exception as exc:  # A transient CLI/API error is recorded and retried.
            fields = ("query_error", "", "")
            query_error = redact(exc)
        now = clock()
        status, message, progress = fields
        record = {
            "poll_utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(now - start, 1),
            "actual_status": redact(status),
            "status_msg": redact(message),
            "image_pull_progress": redact(progress),
        }
        if query_error:
            record["query_error"] = query_error
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

        if status.lower() == "running":
            return 0, "running"
        if status.lower() in {"error", "failed", "exited", "destroyed"}:
            return 42, f"terminal startup status: {status}"
        # status_msg is a potentially large, rolling log string that can change
        # only because the API truncates its leading characters. Do not let that
        # reset the stall clock; explicit image pull progress is extracted above.
        if previous is None or status != previous[0] or progress != previous[2]:
            previous_activity = now
        previous = fields
        elapsed = now - start
        if elapsed >= total_seconds:
            return 42, "startup exceeded 25 minute total limit"
        if elapsed >= stall_seconds and now - previous_activity >= stall_seconds:
            return 42, "no status change or image-pull progress for 8 minutes"
        sleep(min(poll_seconds, max(0.0, total_seconds - elapsed)))


if __name__ == "__main__":
    instance, log = sys.argv[1:]
    code, reason = monitor_startup(instance, log)
    print(reason)
    raise SystemExit(code)
