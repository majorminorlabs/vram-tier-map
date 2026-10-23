#!/usr/bin/env python3
"""Mocked no-rental tests for startup stall, progressing pull, and success."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
from startup_monitor import monitor_startup, status_fields


class FakeClock:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


def test_stall():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as td:
        code, reason = monitor_startup(
            "123", Path(td) / "stall.jsonl",
            fetch=lambda _: {"actual_status": "created", "status_msg": "Creating instance"},
            sleep=clock.sleep, clock=clock.now, poll_seconds=20,
            stall_seconds=60, total_seconds=180,
        )
        assert code == 42 and "8 minutes" in reason
        records = [json.loads(x) for x in (Path(td) / "stall.jsonl").read_text().splitlines()]
        assert len(records) == 4 and records[-1]["actual_status"] == "created"

def test_rolling_status_message_does_not_hide_stall():
    clock = FakeClock(); n = 0
    def fetch(_):
        nonlocal n
        n += 1
        return {"actual_status":"loading", "status_msg":f"rolling log tail {n}"}
    with tempfile.TemporaryDirectory() as td:
        code, reason = monitor_startup("123",Path(td)/"rolling.jsonl",fetch=fetch,
            sleep=clock.sleep,clock=clock.now,poll_seconds=20,stall_seconds=60,total_seconds=180)
        assert code==42 and "8 minutes" in reason


def test_slow_pull():
    clock = FakeClock(); progress = iter([0, 20, 40, 60, 80, 100, "running"])
    def fetch(_):
        value = next(progress)
        if value == "running": return {"actual_status": "running", "status_msg": "Running"}
        return {"actual_status": "created", "status_msg": "Pulling image", "image_pull_progress": value}
    with tempfile.TemporaryDirectory() as td:
        code, reason = monitor_startup(
            "123", Path(td) / "pull.jsonl", fetch=fetch,
            sleep=clock.sleep, clock=clock.now, poll_seconds=20,
            stall_seconds=60, total_seconds=180,
        )
        assert code == 0 and reason == "running"
        records = [json.loads(x) for x in (Path(td) / "pull.jsonl").read_text().splitlines()]
        assert records[-2]["image_pull_progress"].endswith("=100")


def test_running_success():
    clock = FakeClock()
    with tempfile.TemporaryDirectory() as td:
        code, reason = monitor_startup(
            "123", Path(td) / "success.jsonl",
            fetch=lambda _: {"actual_status": "running", "status_msg": "Running"},
            sleep=clock.sleep, clock=clock.now,
        )
        assert (code, reason) == (0, "running")

def test_vast_cur_state_fallback():
    clock = FakeClock()
    payload={"actual_status":None,"status_msg":None,"cur_state":"running","intended_status":"running"}
    assert status_fields(payload) == ("running", "", "")
    with tempfile.TemporaryDirectory() as td:
        code, reason = monitor_startup("123",Path(td)/"cur-state.jsonl",fetch=lambda _:payload,
            sleep=clock.sleep,clock=clock.now)
        assert (code, reason) == (0,"running")

def test_status_message_layer_progress_is_recognized():
    first=status_fields({"actual_status":"loading","status_msg":"abc123abc123: Downloading 10 MB"})
    second=status_fields({"actual_status":"loading","status_msg":"abc123abc123: Download complete"})
    assert first[2] == "abc123abc123:Downloading"
    assert second[2] == "abc123abc123:Download complete"


if __name__ == "__main__":
    test_stall(); test_rolling_status_message_does_not_hide_stall(); test_slow_pull(); test_running_success(); test_vast_cur_state_fallback(); test_status_message_layer_progress_is_recognized()
    print("startup_monitor_stall_slow_pull_success: 6 cases passed")
