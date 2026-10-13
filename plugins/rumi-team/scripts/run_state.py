#!/usr/bin/env python3
"""Local state of a Rumi run, read by the guardrail hook (guard.py).

The guardrails are only active while a run is active in a project, so normal
Claude Code sessions are unaffected. Rumi updates this state at each gate.

  run_state.py start            begin a run in the current project (planning: no building yet)
  run_state.py signed B-2       the user signed off brief B-2: building may start
  run_state.py unsigned         a new brief is awaiting sign-off: building pauses again
  run_state.py allow-host HOST  the user approved contacting HOST (e.g. api.stripe.com)
  run_state.py allow-cmd TEXT   the user approved a command starting with TEXT (e.g. "npm install zod")
  run_state.py finish           the run is over: guardrails switch off for this project
  run_state.py status           print the state

State lives in ~/.claude/rumi-team/runs/<hash of the project path>.json.
"""
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

RUNS = os.path.expanduser("~/.claude/rumi-team/runs")


def project_root(start=None):
    start = os.path.realpath(start or os.getcwd())
    try:
        out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return os.path.realpath(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def state_path(root):
    return os.path.join(RUNS, hashlib.sha1(root.encode()).hexdigest()[:16] + ".json")


def find_state(cwd):
    """The active run for cwd or any parent folder, or None."""
    d = os.path.realpath(cwd)
    while True:
        p = state_path(d)
        if os.path.exists(p):
            try:
                s = json.load(open(p, encoding="utf-8"))
                if s.get("active"):
                    return s
            except (OSError, ValueError):
                return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, arg = sys.argv[1], " ".join(sys.argv[2:]).strip()
    root = project_root()
    path = state_path(root)
    try:
        s = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        s = {}

    if cmd == "start":
        s = {"active": True, "root": root, "briefSigned": False, "signedBrief": None,
             "allowedHosts": [], "allowedCmds": [], "startedAt": now()}
    elif not s:
        sys.exit(f"No Rumi run recorded for {root}. Run 'start' first.")
    elif cmd == "signed":
        s.update(briefSigned=True, signedBrief=arg or None, signedAt=now())
    elif cmd == "unsigned":
        s.update(briefSigned=False)
    elif cmd == "allow-host":
        s["allowedHosts"] = sorted(set(s.get("allowedHosts", [])) | {arg.lower()})
    elif cmd == "allow-cmd":
        s["allowedCmds"] = sorted(set(s.get("allowedCmds", [])) | {" ".join(arg.split())})
    elif cmd == "finish":
        s.update(active=False, finishedAt=now())
    elif cmd != "status":
        sys.exit(__doc__)

    if cmd != "status":
        os.makedirs(RUNS, exist_ok=True)
        json.dump(s, open(path, "w", encoding="utf-8"), indent=2)
    print(json.dumps(s, indent=2))


if __name__ == "__main__":
    main()
