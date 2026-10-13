#!/usr/bin/env python3
"""Token usage and external access for one Claude Code session, per team member.

Reads the session transcript (~/.claude/projects/*/<session>.jsonl) and its
subagent transcripts (<session>/subagents/agent-*.jsonl). Subagents are mapped
to team members by the Agent call's description, which Rumi starts with
"<member> <card>", e.g. "dev-1 R-3 build CSV export". The main thread is "pm".

Usage: telemetry.py [session-id] [--audit-dir DIR]   (session defaults to $CLAUDE_CODE_SESSION_ID)
Prints one compact JSON object for the board's `usage` event field:
  byAgent  tokens per member, with the models each used
  byCard   tokens per requirement
  byModel  tokens per model
  access   per member: websites, searches, databases, data files, connectors,
           package installs and secrets files touched, read or write, with counts
  guard    guardrail blocks and approval requests from the hook (newest 100)

With --audit-dir, it also writes every tool call made since the last run as
JSON chunk files (one board document each) into DIR/batch-<n>/, and prints that
folder on stderr. Calls already exported are remembered in DIR/seen.json.
Secrets that look like keys, tokens or passwords are redacted in the export.
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

MEMBER = re.compile(r"^(pm|dev-\d+|qa-\d+|ba-\d+)\b")
CALLS_PER_CHUNK = 150
CARD = re.compile(r"\b(R-\d+)\b")
KEYS = ("in", "out", "cacheRead", "cacheWrite")
MAX_ACCESS_PER_MEMBER = 40

URL = re.compile(r"https?://[^\s'\"<>)\]]+")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
# A database client only counts in command position: line start or after ; & | ( or $(
DB_CLIENT = re.compile(r"(?:^|[;&|(]|\$\()\s*(?:sudo\s+)?(sqlite3|psql|mysql|mariadb|mongosh|mongo|redis-cli|duckdb|clickhouse-client|bq|snowsql)(?=\s|$)", re.M)
SQL_WRITE = re.compile(r"\b(insert|update|delete|drop|alter|truncate|create\s+table|replace\s+into)\b", re.I)
PKG = re.compile(r"\b((?:npm|pnpm|yarn|bun)\s+(?:install|i|add)\b[^;&|]*|pip3?\s+install\b[^;&|]*|uv\s+(?:add|pip\s+install)\b[^;&|]*"
                 r"|poetry\s+add\b[^;&|]*|brew\s+install\b[^;&|]*|cargo\s+add\b[^;&|]*|go\s+get\b[^;&|]*|gem\s+install\b[^;&|]*)")
# Always data, wherever they live
DATA_EXT = (".csv", ".tsv", ".jsonl", ".ndjson", ".parquet", ".xlsx", ".xls", ".sqlite", ".sqlite3", ".db", ".sql", ".avro", ".feather")
# Data only inside a data-like directory (otherwise they're usually config)
MAYBE_DATA_EXT = (".json", ".yaml", ".yml", ".xml")
DATA_DIRS = ("/data/", "/datasets/", "/fixtures/", "/seed", "/seeds/", "/migrations/", "/dumps/")
CODE_CONFIG = ("package.json", "package-lock.json", "tsconfig.json", "composer.json", ".eslintrc.json", "pnpm-lock.yaml",
               "docker-compose.yml", "docker-compose.yaml")


def host_of(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def classify(name, inp):
    """Yield (kind, target, write) for one tool call."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "WebFetch":
        h = host_of(str(inp.get("url", "")))
        if h:
            yield ("web", h, False)
    elif name == "WebSearch":
        yield ("search", str(inp.get("query", ""))[:80], False)
    elif name.startswith("mcp__"):
        parts = name.split("__")
        server, tool = (parts[1] if len(parts) > 1 else "mcp"), (parts[-1] if len(parts) > 2 else "")
        url = inp.get("url") if isinstance(inp.get("url"), str) else ""
        if url and host_of(url) and host_of(url) not in LOCAL_HOSTS:
            yield ("web", host_of(url), False)
        elif "browser" not in server.lower() and "chrome" not in server.lower():
            write = bool(re.search(r"(create|update|delete|send|write|set|post|trash|move|upload|reply|forward)", tool, re.I))
            yield ("connector", f"{server}:{tool}"[:80], write)
    elif name == "Bash":
        cmd = str(inp.get("command", ""))
        for u in URL.findall(cmd):
            h = host_of(u)
            if h and h not in LOCAL_HOSTS:
                yield ("web", h, bool(re.search(r"-X\s*(POST|PUT|PATCH|DELETE)|--data|-d\s", cmd)))
        for m in DB_CLIENT.finditer(cmd):
            rest = cmd[m.end():].split()
            target = next((a for a in rest if not a.startswith("-")), "")
            yield ("database", (m.group(1) + (" " + target if target else ""))[:80], bool(SQL_WRITE.search(cmd)))
        for m in PKG.finditer(cmd):
            yield ("package", " ".join(m.group(1).split())[:80], True)
        if re.search(r"(^|[\s/])\.env(\.[\w.-]+)?\b", cmd) and re.search(r"\b(cat|less|head|tail|source|grep|sed)\b", cmd):
            yield ("secrets", ".env (via shell)", False)
    elif name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        base = os.path.basename(path)
        write = name != "Read"
        if re.match(r"^\.env(\.|$)", base) or base.endswith((".pem", ".key")) or "credentials" in base.lower():
            yield ("secrets", base, write)
        elif base not in CODE_CONFIG and (path.lower().endswith(DATA_EXT)
                                          or (path.lower().endswith(MAYBE_DATA_EXT) and any(d in path for d in DATA_DIRS))):
            yield ("data-file", path[-80:], write)


REDACT = [
    (re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd|auth)(\s*[=:]\s*)(['\"]?)[^\s'\"&]{6,}"), r"\1\2\3[redacted]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[redacted]"),
    (re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp])[-_][A-Za-z0-9_-]{12,}"), "[redacted]"),
    (re.compile(r"(://[^/\s:@]+:)[^@\s/]+@"), r"\1[redacted]@"),
]


def redact(text):
    for pat, sub in REDACT:
        text = pat.sub(sub, text)
    return text


def describe(name, inp):
    """One readable line for the audit log."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        d = inp.get("command", "")
    elif name in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit"):
        d = inp.get("file_path") or inp.get("notebook_path") or ""
    elif name == "Grep":
        d = f'{inp.get("pattern", "")}  in {inp.get("path") or inp.get("glob") or "."}'
    elif name == "Glob":
        d = inp.get("pattern", "")
    elif name == "WebFetch":
        d = inp.get("url", "")
    elif name == "WebSearch":
        d = inp.get("query", "")
    elif name in ("Agent", "Task"):
        d = f'{inp.get("subagent_type", "")}: {inp.get("description", "")}'
    elif name == "ArtifactData":
        d = " ".join(str(x) for x in (inp.get("action"), inp.get("collection"), inp.get("doc_id")) if x)
        if inp.get("writes"):
            d += f' ({len(inp["writes"])} writes)'
    else:
        d = json.dumps(inp, separators=(",", ":"), ensure_ascii=False)
    return redact(" ".join(str(d).split()))[:300]


def scan(path, main=False):
    """Return (tokens, {model: tokens}, {(kind, target, write): [count, first, last]}, [tool calls])."""
    t, models, last, seen_tools, access = dict.fromkeys(KEYS, 0), {}, {}, set(), {}
    calls, by_id = [], {}
    try:
        lines = open(path, encoding="utf-8")
    except OSError:
        return t, models, access, calls
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if main and d.get("isSidechain"):
            continue
        if d.get("type") == "user":  # tool results: mark failed calls
            for block in (d.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                    by_id[block["tool_use_id"]]["ok"] = not block.get("is_error")
            continue
        if d.get("type") != "assistant":
            continue
        msg = d.get("message") or {}
        ts = d.get("timestamp", "")
        for block in msg.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("id") in seen_tools:
                continue
            seen_tools.add(block.get("id"))
            tname = str(block.get("name", ""))
            kinds = list(classify(tname, block.get("input")))
            call = {"id": block.get("id"), "ts": ts, "tool": tname, "detail": describe(tname, block.get("input")), "ok": None}
            if kinds:
                call["kind"] = kinds[0][0]
                call["write"] = any(k[2] for k in kinds)
            calls.append(call)
            by_id[call["id"]] = call
            for key in kinds:
                rec = access.setdefault(key, [0, ts, ts])
                rec[0] += 1
                rec[2] = ts or rec[2]
        u = msg.get("usage")
        model = msg.get("model") or "unknown"
        if not u or model.startswith("<"):  # skip "<synthetic>" placeholder messages
            continue
        # One API response is logged once per content block; later copies carry
        # the final output count, so keep the last one seen for each response.
        key = msg.get("id") or d.get("requestId") or d.get("uuid")
        last[key] = (model, {
            "in": u.get("input_tokens", 0) or 0,
            "out": u.get("output_tokens", 0) or 0,
            "cacheRead": u.get("cache_read_input_tokens", 0) or 0,
            "cacheWrite": u.get("cache_creation_input_tokens", 0) or 0,
        })
    for model, row in last.values():
        m = models.setdefault(model, dict.fromkeys(KEYS, 0))
        for k in KEYS:
            t[k] += row[k]
            m[k] += row[k]
    return t, models, access, calls


def add(into, t, models, by_model, runs=1):
    for k in KEYS:
        into[k] = into.get(k, 0) + t[k]
    into["runs"] = into.get("runs", 0) + runs
    per = into.setdefault("models", {})
    for model, mt in models.items():
        per[model] = per.get(model, 0) + sum(mt.values())
        g = by_model.setdefault(model, dict.fromkeys(KEYS, 0))
        for k in KEYS:
            g[k] += mt[k]


def merge_access(into, found):
    for key, (n, first, last) in found.items():
        rec = into.setdefault(key, [0, first, last])
        rec[0] += n
        rec[1] = min(filter(None, [rec[1], first]), default="")
        rec[2] = max(filter(None, [rec[2], last]), default="")


def export_calls(audit_dir, sid, calls):
    """Write calls not exported before as chunk files: one board document each."""
    os.makedirs(audit_dir, exist_ok=True)
    seen_path = os.path.join(audit_dir, "seen.json")
    try:
        state = json.load(open(seen_path, encoding="utf-8"))
    except (OSError, ValueError):
        state = {"seen": [], "batches": 0, "seq": {}}
    seen = set(state["seen"])
    state["batches"] += 1
    batch = os.path.join(audit_dir, f"batch-{state['batches']}")
    written = 0
    for member, cl in calls.items():
        fresh = [c for c in cl if c["id"] not in seen and c["ok"] is not None]  # skip calls still running
        for i in range(0, len(fresh), CALLS_PER_CHUNK):
            chunk = fresh[i:i + CALLS_PER_CHUNK]
            seq = state["seq"].get(member, 0) + 1
            state["seq"][member] = seq
            os.makedirs(batch, exist_ok=True)
            doc_id = f"{sid[:8]}-{member}-{seq:04d}"
            with open(os.path.join(batch, doc_id + ".json"), "w", encoding="utf-8") as f:
                json.dump({"session": sid, "member": member, "seq": seq, "from": chunk[0]["ts"], "to": chunk[-1]["ts"],
                           "calls": [{k: v for k, v in c.items() if k != "id"} for c in chunk]}, f, separators=(",", ":"))
            seen.update(c["id"] for c in chunk)
            written += 1
    state["seen"] = sorted(seen)
    json.dump(state, open(seen_path, "w", encoding="utf-8"))
    print(f"audit: {written} chunk file(s) in {batch}" if written else "audit: no new tool calls", file=sys.stderr)


def main():
    args = sys.argv[1:]
    audit_dir = None
    if "--audit-dir" in args:
        i = args.index("--audit-dir")
        audit_dir = os.path.expanduser(args[i + 1])
        del args[i:i + 2]
    sid = args[0] if args else os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not sid:
        sys.exit("No session id: pass one or set CLAUDE_CODE_SESSION_ID")
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    if not hits:
        sys.exit(f"No transcript found for session {sid}")
    main_path = hits[0]
    by_agent, by_card, by_model, access, calls, agent_member = {}, {}, {}, {}, {}, {}

    t, models, acc, cl = scan(main_path, main=True)
    add(by_agent.setdefault("pm", {}), t, models, by_model)
    merge_access(access.setdefault("pm", {}), acc)
    calls.setdefault("pm", []).extend(cl)

    for path in sorted(glob.glob(os.path.join(main_path[:-6], "subagents", "agent-*.jsonl"))):
        meta = {}
        try:
            meta = json.load(open(path[:-6] + ".meta.json", encoding="utf-8"))
        except (OSError, ValueError):
            pass
        desc = str(meta.get("description", ""))
        m = MEMBER.match(desc)
        member = m.group(1) if m else "other"
        agent_member[os.path.basename(path)[len("agent-"):-len(".jsonl")]] = member
        t, models, acc, cl = scan(path)
        add(by_agent.setdefault(member, {}), t, models, by_model)
        merge_access(access.setdefault(member, {}), acc)
        calls.setdefault(member, []).extend(cl)
        c = CARD.search(desc)
        if c:
            by_card[c.group(1)] = by_card.get(c.group(1), 0) + sum(t.values())

    access_out = {}
    for member, recs in access.items():
        rows = [{"kind": k, "target": tg, "write": w, "count": n, "first": f, "last": l}
                for (k, tg, w), (n, f, l) in recs.items()]
        rows.sort(key=lambda r: (not r["write"], -r["count"]))
        if rows:
            access_out[member] = rows[:MAX_ACCESS_PER_MEMBER]

    guard = []
    try:
        for line in open(os.path.expanduser(f"~/.claude/rumi-team/guard/{sid}.jsonl"), encoding="utf-8"):
            try:
                g = json.loads(line)
            except ValueError:
                continue
            aid = g.get("agentId")
            guard.append({"ts": g.get("ts"), "member": agent_member.get(aid, "other") if aid else "pm", "tool": g.get("tool"),
                          "detail": g.get("detail"), "decision": g.get("decision"), "reason": g.get("reason")})
    except OSError:
        pass

    if audit_dir:
        export_calls(audit_dir, sid, calls)

    print(json.dumps({
        "session": sid,
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "byAgent": by_agent,
        "byCard": by_card,
        "byModel": by_model,
        "access": access_out,
        "guard": guard[-100:],
    }, separators=(",", ":")))


if __name__ == "__main__":
    main()
