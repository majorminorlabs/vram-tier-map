#!/usr/bin/env python3
"""Safely log a Vast create response without emitting credential material."""
import json, re, sys
raw = sys.stdin.buffer.read()
secret_key = re.compile(r"(?i)(api.?key|token|secret|authorization|password|credential)")
patterns = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)(vastai[-_])[A-Za-z0-9._~-]{12,}"),
    re.compile(r"(?i)(api.?key\s*[:=]\s*)['\"]?[A-Za-z0-9._~-]{12,}"),
]
def clean(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if secret_key.search(str(k)) else clean(v)) for k,v in value.items()}
    if isinstance(value, list): return [clean(v) for v in value]
    if isinstance(value, str):
        for rx in patterns: value = rx.sub(r"\1[REDACTED]", value)
        value = re.sub(r"(?<![A-Za-z0-9])[A-Za-z0-9._~+/=:@%-]{32,}(?![A-Za-z0-9])", "[REDACTED]", value)
        return value
    return value
try:
    obj=json.loads(raw)
    output=clean(obj)
except Exception:
    # An opaque response is safer to omit than to guess which substrings are secrets.
    output={"response":"[OMITTED: non-JSON response redacted]","length_bytes":len(raw)}
json.dump(output,sys.stdout,indent=2,sort_keys=True)
print()
