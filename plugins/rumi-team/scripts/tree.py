#!/usr/bin/env python3
"""Snapshot of the project's folder structure for the board's Project files panel.

Usage: tree.py [project-dir] --out FILE
Writes one JSON document (a board doc) to FILE and prints FILE's path.
In a git repo it lists tracked and untracked files that aren't ignored;
otherwise it walks the folder, skipping dependency, build and cache folders.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

MAX_FILES = 2500
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next", ".nuxt", "target",
             ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode", "coverage", ".turbo", ".gradle",
             "Pods", "DerivedData", ".terraform"}


def git_files(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                             capture_output=True, check=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def walk_files(root):
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.endswith(".egg-info"))
        for n in sorted(names):
            if n == ".DS_Store":
                continue
            files.append(os.path.relpath(os.path.join(d, n), root))
            if len(files) > MAX_FILES:
                return files
    return files


def git_branch(root):
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def main():
    args = sys.argv[1:]
    if "--out" not in args:
        sys.exit("usage: tree.py [project-dir] --out FILE")
    i = args.index("--out")
    out = os.path.expanduser(args[i + 1])
    del args[i:i + 2]
    root = os.path.abspath(args[0] if args else os.getcwd())

    files = git_files(root)
    source = "git" if files is not None else "walk"
    if files is None:
        files = walk_files(root)
    files = sorted(set(files))
    truncated = len(files) > MAX_FILES
    files = files[:MAX_FILES]

    entries = []
    for rel in files:
        try:
            size = os.path.getsize(os.path.join(root, rel))
        except OSError:
            continue  # deleted but still in the index
        entries.append([rel, size])

    now = datetime.now(timezone.utc)
    doc = {
        "generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "root": root,
        "name": os.path.basename(root),
        "branch": git_branch(root) if source == "git" else None,
        "source": source,
        "fileCount": len(entries),
        "truncated": truncated,
        "files": entries,  # [relative path, size in bytes]
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    print(out)


if __name__ == "__main__":
    main()
