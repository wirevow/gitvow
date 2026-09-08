"""Gather what a trial produced on this machine into one redacted directory, and compute trial metrics."""

from __future__ import annotations

import collections
import glob
import json
import os
import shutil
import time
from typing import Any

from .state import git


def collect(home: str, out_dir: str) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    w = os.path.join(out_dir, f"gitvow-{stamp}")
    os.makedirs(os.path.join(w, "ledger"), exist_ok=True)
    os.makedirs(os.path.join(w, "repos"), exist_ok=True)
    led = os.path.join(home, ".gitvow", "ledger")
    repos: set[str] = set()
    for f in glob.glob(os.path.join(led, "*.json")):
        shutil.copy(f, os.path.join(w, "ledger"))
        try:
            with open(f) as fh:
                r = json.load(fh).get("repo")
            if r:
                repos.add(r)
        except (OSError, json.JSONDecodeError):
            pass
    for r in sorted(repos):
        if not os.path.isdir(r):
            continue
        d = os.path.join(w, "repos", os.path.basename(r.rstrip("/")))
        os.makedirs(d, exist_ok=True)
        rc, gd, _ = git(["rev-parse", "--git-dir"], r)
        if rc != 0:
            continue
        gd = gd if os.path.isabs(gd) else os.path.join(r, gd)
        lp = os.path.join(gd, "gitvow-hooks.log")
        if os.path.exists(lp):
            shutil.copy(lp, os.path.join(d, "gitvow-hooks.log"))
        for name, args in (
            ("commits-with-trailers.txt", ["log", "--format=%H %ad %s", "--date=short", "--grep=Gitvow-Session:"]),
            ("notes.txt", ["log", "--show-notes=sessions", "--format=%H%n%N%n----", "--grep=Gitvow-Session:"]),
            ("remote.txt", ["remote", "get-url", "origin"]),
        ):
            _, out, _ = git(args, r)
            with open(os.path.join(d, name), "w") as fh:
                fh.write(out)
    with open(os.path.join(w, "SUMMARY.txt"), "w") as fh:
        fh.write(summarize_text(w))
    return w


def summarize(w: str) -> dict[str, Any]:
    led = [json.load(open(f)) for f in glob.glob(os.path.join(w, "ledger", "*.json"))]  # noqa: SIM115
    tools: collections.Counter[str] = collections.Counter(t.get("tool") for s in led for t in s.get("tool_calls", []))
    kinds: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[tuple[str, str]] = collections.Counter()
    trailered = notes = 0
    for lp in glob.glob(os.path.join(w, "repos", "*", "gitvow-hooks.log")):
        with open(lp) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kinds[e.get("kind", "?")] += 1
                if e.get("kind") in ("blocked", "confirm_required"):
                    reasons[(e.get("kind", "?"), e.get("reason", "?"))] += 1
    for f in glob.glob(os.path.join(w, "repos", "*", "commits-with-trailers.txt")):
        with open(f) as fh:
            trailered += sum(1 for ln in fh if ln.strip())
    for f in glob.glob(os.path.join(w, "repos", "*", "notes.txt")):
        with open(f) as fh:
            notes += fh.read().count("gitvow-session")
    return {
        "sessions": len(led),
        "repos": len({s.get("repo") for s in led}),
        "tool_calls": sum(tools.values()),
        "by_tool": dict(tools.most_common(8)),
        "commits_during_sessions": sum(len(s.get("commits_during_session", [])) for s in led),
        "hook_decisions": dict(kinds),
        "commits_with_trailers": trailered,
        "notes_attached": notes,
        "gate_fired_on": [{"kind": k, "reason": r, "n": n} for (k, r), n in reasons.most_common(20)],
    }


def summarize_text(w: str) -> str:
    s = summarize(w)
    lines = [
        f"sessions: {s['sessions']}   repos touched: {s['repos']}",
        f"tool calls: {s['tool_calls']} | by tool: {s['by_tool']}",
        f"commits during sessions: {s['commits_during_sessions']}",
        f"hook decisions: {s['hook_decisions']}",
        f"commits with trailers: {s['commits_with_trailers']} | notes attached: {s['notes_attached']}",
        "gate fired on:",
    ]
    lines += [f"  {g['kind']:<17} {g['n']:>4}  {g['reason']}" for g in s["gate_fired_on"]] or ["  (never)"]
    return "\n".join(lines) + "\n"
