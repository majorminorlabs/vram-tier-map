#!/usr/bin/env python3
"""Kill one smoke-run parent with SIGKILL and verify watchdog cleanup in 120s."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def instances():
    p = subprocess.run(["vastai", "show", "instances", "--raw"], capture_output=True, text=True)
    if p.returncode:
        return None
    try:
        data = json.loads(p.stdout)
        return data if isinstance(data, list) else data.get("instances", [])
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--startup-timeout", type=int, default=900)
    ap.add_argument("--kill-delay", type=int, default=15)
    ap.add_argument("--cleanup-timeout", type=int, default=120)
    args = ap.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    label = "vtm-" + args.run_id
    marker = output / "kill-test-running"
    marker.unlink(missing_ok=True)
    log_path = output / "kill-test-main.log"
    command = ["bash", str(Path(__file__).with_name("vast_run.sh")), "smoke-kill", "any", "0.15", str(output), args.run_id]
    started = time.monotonic()
    with log_path.open("w") as log:
        proc = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[2], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + args.startup_timeout
        while time.monotonic() < deadline and not marker.exists():
            if proc.poll() is not None:
                break
            time.sleep(1)
        if not marker.exists():
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            raise SystemExit(f"watchdog smoke failed before run reached kill marker; see {log_path}")
        time.sleep(args.kill_delay)
        if proc.poll() is not None:
            raise SystemExit(f"watchdog smoke main process exited before SIGKILL; see {log_path}")
        killed_at = time.monotonic()
        os.kill(proc.pid, signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    destroyed_at = None
    deadline = killed_at + args.cleanup_timeout
    while time.monotonic() < deadline:
        rows = instances()
        if rows is not None and not rows:
            destroyed_at = time.monotonic()
            break
        time.sleep(5)

    elapsed = round((destroyed_at or time.monotonic()) - killed_at, 2)
    passed = destroyed_at is not None and elapsed <= args.cleanup_timeout
    result = {
        "run_id": args.run_id,
        "label": label,
        "main_killed_with": "SIGKILL",
        "watchdog_cleanup_seconds": elapsed if passed else None,
        "zero_instances_after": passed,
        "passed": passed,
        "main_log": str(log_path),
    }
    (output / "kill-test-result.json").write_text(json.dumps(result, indent=2) + "\n")
    if not passed:
        script_dir = Path(__file__).resolve().parent
        rescue = subprocess.run([str(script_dir / "destroy_label.sh"), label, str(output / "rescue-teardown.log")], check=False)
        if rescue.returncode:
            subprocess.run([str(script_dir / "cleanup_orphans.sh"), "", str(output / "rescue-orphans.log")], check=False)
        rows = instances()
        count = len(rows) if rows is not None else "unknown"
        raise SystemExit(f"watchdog did not confirm zero instances within {args.cleanup_timeout}s; rescue teardown attempted; remaining count={count}")
    print(f"watchdog smoke passed: SIGKILL cleanup completed in {elapsed}s; zero instances")


if __name__ == "__main__":
    main()
