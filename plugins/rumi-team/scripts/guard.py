#!/usr/bin/env python3
"""PreToolUse guardrail hook for Rumi's agent team.

Runs before every tool call. It does nothing unless a Rumi run is active in the
current project (see run_state.py), so ordinary sessions are never affected.
During a run it:
  - denies file edits until the brief is signed off
  - denies reading or editing secrets (.env, keys, credentials)
  - denies deleting outside the project and force-pushing
  - asks the user before package installs, database writes, git commit/push,
    shell network calls to unapproved hosts, and connector actions that send or change things
Every deny and ask is appended to ~/.claude/rumi-team/guard/<session>.jsonl for the board.
"""
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run_state import find_state  # noqa: E402
from telemetry import DB_CLIENT, LOCAL_HOSTS, PKG, SQL_WRITE, URL, host_of, reads_secret_file, redact, strip_heredocs  # noqa: E402

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
FILE_TOOLS = EDIT_TOOLS | {"Read"}
SAFE_ENV = {".env.example", ".env.sample", ".env.template", ".env.dist"}
HOME = os.path.expanduser("~")
TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/", os.path.join(HOME, ".claude") + "/")
SHELL_WRITE = re.compile(r"(^|[;&|(]\s*)(mkdir|touch|rm|mv|cp|ln|chmod|sed\s+-i|tee|npm\s+init|npx\s+create|pnpm\s+create|yarn\s+create|"
                         r"django-admin|rails\s+new|cargo\s+new|go\s+mod\s+init|git\s+(init|add|commit|checkout|reset|clean|mv|rm))\b")
REDIRECT = re.compile(r"(?<![0-9&<>])>{1,2}\s*(?!&)(\S+)")
NET_CMD = re.compile(r"(^|[;&|(]\s*)(curl|wget|http|https|xh)\b")
GIT_COMMIT_PUSH = re.compile(r"\bgit\s+(commit|push)\b")
FORCE_PUSH = re.compile(r"\bgit\s+push\b.*(\s--force\b|\s-f\b|\s--force-with-lease\b)")
HARD_RESET = re.compile(r"\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f)")
RM_RF = re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+(.+)")
CONNECTOR_WRITE = re.compile(r"(create|update|delete|send|write|set|post|trash|move|upload|reply|forward|share|publish)", re.I)


def is_secret(path):
    base = os.path.basename(str(path))
    if base in SAFE_ENV:
        return False
    return bool(re.match(r"^\.env(\..+)?$", base) or base.endswith((".pem", ".key", ".p12", ".pfx"))
                or base.startswith("id_rsa") or base.startswith("id_ed25519")
                or re.match(r"^(credentials|secrets?)(\..+)?$", base, re.I))


def is_scratch(path, root):
    p = os.path.realpath(os.path.expanduser(str(path)))
    return p.startswith(TEMP_PREFIXES) or "/.rumi-board-setup/" in p + "/"


def inside(path, root):
    p = os.path.realpath(os.path.join(root, os.path.expanduser(path)))
    return p == root or p.startswith(root + "/")


def approved(cmd, state):
    norm = " ".join(cmd.split())
    return any(norm.startswith(a) or f" {a}" in norm for a in state.get("allowedCmds", []))


def check(tool, ti, state):
    """Return (decision, reason) or None to allow."""
    root = state.get("root") or os.getcwd()
    signed = state.get("briefSigned")

    if tool in FILE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if is_secret(path):
            return "deny", f"Guardrail: {os.path.basename(path)} holds secrets. Agents never read or change secret files. Use .env.example with placeholder values, and ask Rumi to raise a needs-you item if a real setting is needed."
        if tool in EDIT_TOOLS and not signed and not is_scratch(path, root):
            return "deny", "Guardrail: the brief and definition of done haven't been signed off yet, so no project files can be created or changed. Rumi will continue once the user signs off on the board."
        return None

    if tool == "Bash":
        cmd = strip_heredocs(str(ti.get("command", "")))  # file contents being written aren't commands
        if reads_secret_file(cmd):
            return "deny", "Guardrail: that command would read or copy a secrets file (.env). Agents never touch secrets."
        if FORCE_PUSH.search(cmd):
            return "deny", "Guardrail: force-pushing can destroy other people's work and is never allowed during a run."
        m = RM_RF.search(cmd)
        if m:
            for target in shlex.split(m.group(2), posix=True) if m.group(2) else []:
                if target.startswith("-") or target in ("&&", "||", ";", "|"):
                    break
                if not inside(target, root) and not is_scratch(target, root):
                    return "deny", f"Guardrail: deleting '{target}' is outside the project folder, which is never allowed."
                if os.path.realpath(os.path.join(root, os.path.expanduser(target))) == root:
                    return "deny", "Guardrail: deleting the whole project folder is never allowed."
        if not signed:
            writes = SHELL_WRITE.search(cmd)
            redirect = next((t for t in REDIRECT.findall(cmd) if t not in ("/dev/null",) and not is_scratch(t, root)), None)
            if (writes or redirect) and ".claude/rumi-team" not in cmd:
                return "deny", "Guardrail: the brief hasn't been signed off yet, so commands that create or change project files aren't allowed. Reading and planning are fine."
        if approved(cmd, state):
            return None
        if HARD_RESET.search(cmd):
            return "ask", "Rumi's team wants to discard uncommitted changes (git reset --hard / git clean). Allow?"
        pkg = PKG.search(cmd)
        if pkg:
            return "ask", f"Rumi's team wants to install software: `{' '.join(pkg.group(1).split())[:120]}`. Allow?"
        db = DB_CLIENT.search(cmd)
        if db and SQL_WRITE.search(cmd) and not re.search(r":memory:|test|fixture", cmd, re.I):
            return "ask", f"Rumi's team wants to change data in a database with {db.group(1)}. Allow?"
        gm = GIT_COMMIT_PUSH.search(cmd)
        if gm:
            return "ask", f"Rumi's team wants to run `git {gm.group(1)}`. Allow?"
        if NET_CMD.search(cmd):
            hosts = {host_of(u) for u in URL.findall(cmd)} - LOCAL_HOSTS - {""}
            new = sorted(h for h in hosts if h not in state.get("allowedHosts", []))
            if new:
                return "ask", f"Rumi's team wants to contact {', '.join(new)} from the shell. Allow?"
        return None

    if tool.startswith("mcp__"):
        name = tool.split("__")[-1]
        server = tool.split("__")[1] if tool.count("__") >= 2 else ""
        if "browser" in server.lower() or "chrome" in server.lower():
            return None
        if CONNECTOR_WRITE.search(name):
            return "ask", f"Rumi's team wants to use a connected app to {name.replace('_', ' ')}. Allow?"
    return None


def log(inp, decision, reason):
    sid = inp.get("session_id") or "unknown"
    d = os.path.expanduser("~/.claude/rumi-team/guard")
    os.makedirs(d, exist_ok=True)
    ti = inp.get("tool_input") or {}
    detail = ti.get("command") or ti.get("file_path") or ti.get("url") or json.dumps(ti)[:200]
    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tool": inp.get("tool_name"),
           "detail": redact(" ".join(str(detail).split()))[:300], "decision": decision, "reason": reason,
           "toolUseId": inp.get("tool_use_id"), "agentId": inp.get("agent_id"), "agentType": inp.get("agent_type")}
    with open(os.path.join(d, f"{sid}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def main():
    try:
        inp = json.load(sys.stdin)
    except ValueError:
        return  # never break a session over unreadable input
    state = find_state(inp.get("cwd") or os.getcwd())
    if not state:
        return
    result = check(str(inp.get("tool_name", "")), inp.get("tool_input") or {}, state)
    if not result:
        return
    decision, reason = result
    try:
        log(inp, decision, reason)
    except OSError:
        pass
    out = {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}
    if decision == "deny":
        # The reason alone isn't shown to the model; additionalContext is, so the agent knows why and what to do.
        out["additionalContext"] = reason + " Don't try to work around this guardrail; report it so Rumi can ask the user."
    print(json.dumps({"hookSpecificOutput": out}))


if __name__ == "__main__":
    main()
