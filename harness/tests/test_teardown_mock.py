#!/usr/bin/env python3
"""Exercise every teardown entry point against a mock Vast CLI."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "harness" / "scripts"
EXPECTED_DESTROY = ["destroy", "instance", "42", "--yes", "--raw"]

MOCK_VASTAI = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args=sys.argv[1:]
with open(os.environ["MOCK_VAST_LOG"],"a") as f: f.write(json.dumps(args)+"\n")
state=pathlib.Path(os.environ["MOCK_VAST_STATE"])
if args == ["show","instances","--raw"]:
    try: item=json.loads(state.read_text())
    except FileNotFoundError: item=None
    print(json.dumps([item] if item else []))
elif args == ["destroy","instance","42","--yes","--raw"]:
    state.unlink(missing_ok=True)
    print("{}")
else:
    print("unexpected mocked Vast argv",file=sys.stderr)
    raise SystemExit(2)
'''


def run_case(name, command, instance):
    with tempfile.TemporaryDirectory(prefix=f"vtm-{name}-") as temp:
        temp = Path(temp)
        bindir = temp / "bin"
        bindir.mkdir()
        mock = bindir / "vastai"
        mock.write_text(MOCK_VASTAI)
        mock.chmod(0o755)
        state = temp / "instance.json"
        state.write_text(json.dumps(instance))
        if name == "watchdog":
            wd_state = temp / "state"
            wd_state.mkdir()
            (wd_state / "active-instance-id.txt").write_text("42\n")
        log = temp / "argv.jsonl"
        env = os.environ.copy()
        env.update(PATH=f"{bindir}:{env['PATH']}", MOCK_VAST_LOG=str(log), MOCK_VAST_STATE=str(state))
        subprocess.run(command(temp), cwd=ROOT, env=env, check=True, timeout=30)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        destroys = [args for args in calls if args[:2] == ["destroy", "instance"]]
        assert destroys == [EXPECTED_DESTROY], f"{name}: unexpected destroy argv {destroys!r}"
        assert not state.exists(), f"{name}: mock instance was not destroyed"
        print(f"{name}: passed; destroy argv includes --yes")


def make_instance(label="vtm-test"):
    return {"id": 42, "label": label}


def main():
    run_case("main_destroy", lambda t: [str(SCRIPTS / "destroy_instance.sh"), "42", "vtm-test", str(t / "log")], make_instance())
    run_case("exit_trap_teardown", lambda t: [str(SCRIPTS / "teardown.sh"), "42", "vtm-test", str(t / "state")], make_instance())
    run_case("watchdog", lambda t: [str(SCRIPTS / "watchdog.sh"), "vtm-test", "999999999", "0", str(t / "state")], make_instance())
    run_case("orphan_cleanup", lambda t: [str(SCRIPTS / "cleanup_orphans.sh"), "", str(t / "log")], make_instance("vtm-orphan"))
    run_case("label_cleanup", lambda t: [str(SCRIPTS / "destroy_label.sh"), "vtm-test", str(t / "log")], make_instance())


if __name__ == "__main__":
    main()
