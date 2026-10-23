#!/usr/bin/env python3
"""Run one create attempt and persist redacted stdout/stderr diagnostics."""
import json
import re
import subprocess
import sys
from pathlib import Path

run_id, label, offer, image, disk, logdir, attempt = sys.argv[1:]
logdir = Path(logdir)
logdir.mkdir(parents=True, exist_ok=True)
base = f"{run_id}-attempt-{int(attempt):02d}"
p = subprocess.run(
    ["vastai", "create", "instance", offer, "--image", image, "--disk", disk,
     "--label", label, "--ssh", "--direct", "--cancel-unavail", "--raw"],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
)
raw_out = p.stdout.decode("utf-8", "replace")
raw_err = p.stderr.decode("utf-8", "replace")
# Redact bearer/API-key forms, then any token-like run of 32+ characters.
def redact(text):
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(vastai[-_])[A-Za-z0-9._~-]{12,}", r"\1[REDACTED]", text)
    pattern = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9._~+/=:@%-]{32,}(?![A-Za-z0-9])")
    return pattern.sub("[REDACTED]", text)

safe_err = redact(raw_err)
(logdir / f"{base}.stderr.txt").write_text(safe_err, encoding="utf-8")
# Stable run-level stderr file contains all attempts in order.
with (logdir / f"{run_id}.stderr.txt").open("a", encoding="utf-8") as stream:
    stream.write(f"=== attempt {attempt} offer {offer} ===\n{safe_err}\n")
try:
    obj = json.loads(raw_out)
    json_ok = isinstance(obj, dict) and obj.get("success") is True and obj.get("new_contract") not in (None, "")
except Exception:
    obj = None
    json_ok = False
redactor = subprocess.run(
    [sys.executable, str(Path(__file__).with_name("redact_create_response.py"))],
    input=p.stdout, stdout=subprocess.PIPE, check=True,
)
(logdir / f"{base}.json").write_bytes(redactor.stdout)
(logdir / f"{run_id}.json").write_bytes(redactor.stdout)
status = None
if isinstance(obj, dict):
    for key in ("http_status", "status_code", "status"):
        value = obj.get(key)
        if isinstance(value, int) and 100 <= value <= 599:
            status = value
            break
for rx in (
    r"(?i)\bHTTP(?:/\d(?:\.\d)?)?\s+(?:status\s*(?:code\s*)?)?(\d{3})\b",
    r"(?i)\bstatus(?:_code)?\s*[:=]\s*(\d{3})\b",
):
    match = re.search(rx, safe_err) or re.search(rx, redact(raw_out))
    if match:
        status = int(match.group(1))
        break
error_class = None
for needle, name in (("unauthorized", "Unauthorized"), ("forbidden", "Forbidden"),
                     ("authentication", "AuthenticationError"), ("authorization", "AuthorizationError"),
                     ("timeout", "TimeoutError"), ("connection", "ConnectionError"), ("error", "VastCliError")):
    if needle in safe_err.lower():
        error_class = name
        break
summary_path = logdir / f"{run_id}.stderr-summary.json"
try:
    summaries = json.loads(summary_path.read_text())
except Exception:
    summaries = {"attempts": []}
summaries.setdefault("attempts", []).append({
    "attempt": int(attempt), "offer_id": offer, "exit_code": p.returncode,
    "http_status": status, "error_class": error_class,
    "stderr_bytes": len(p.stderr), "stdout_json_success": bool(json_ok),
})
summary_path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
if p.returncode != 0 or not json_ok:
    sys.exit(1)
sys.stdout.write(raw_out)
