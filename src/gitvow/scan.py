"""What an existing repository already shows, before gitvow is installed anywhere.

`gitvow scan` reads `git log` and nothing else: how much of the recent history an agent wrote, how much of
that touched files the policy calls consequential, and how much of it records who agreed. It writes nothing,
needs no configuration and works on a repository that has never heard of gitvow.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .decisions import parse_trailers
from .policy import DEFAULT_POLICY_PATH, PolicyError, load_policy
from .state import git, toplevel

# How an agent signs its own work. Trailers first, then the phrases agents put in a message body.
AGENT_MARKS: tuple[tuple[str, str], ...] = (
    ("Claude", r"Co-?authored-?by:[^\n]*(claude|anthropic)"),
    ("Claude", r"Generated with \[Claude Code\]"),
    ("Cursor", r"Co-?authored-?by:[^\n]*cursor"),
    ("Copilot", r"Co-?authored-?by:[^\n]*copilot"),
    ("Codex", r"Co-?authored-?by:[^\n]*(codex|chatgpt)"),
    ("Gemini", r"Co-?authored-?by:[^\n]*(gemini|google-labs-jules)"),
    ("Devin", r"Co-?authored-?by:[^\n]*devin"),
    ("Aider", r"Co-?authored-?by:[^\n]*aider"),
    ("Factory", r"Co-?authored-?by:[^\n]*(factory|droid)"),
    ("Windsurf", r"Co-?authored-?by:[^\n]*(windsurf|codeium)"),
)
COMPILED = tuple((name, re.compile(pat, re.I)) for name, pat in AGENT_MARKS)
SESSION_RE = re.compile(r"^Gitvow-Session:\s*\S+", re.M)
MAX_COMMITS = 5000


def _since_date(since: str) -> str:
    m = re.fullmatch(r"(\d+)([dwmy])", since or "")
    if not m:
        return since
    n, unit = int(m.group(1)), m.group(2)
    days = n * {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))


def _policy_source(cwd: str, home: str | None = None) -> tuple[dict[str, Any], str]:
    """The policy load_policy would use, and where it came from, so the scan can say whose rules it applied."""
    home = home or os.path.expanduser("~")
    for path, label in (
        (os.path.join(cwd, ".gitvow", "policy.json"), "this repository's policy"),
        (os.path.join(home, ".gitvow", "policy.json"), "your policy"),
    ):
        if os.path.exists(path):
            try:
                return load_policy(cwd, home), label
            except PolicyError:
                break
    with open(DEFAULT_POLICY_PATH) as fh:
        return json.load(fh), "the default policy"


def _patterns(cwd: str) -> tuple[list[tuple[re.Pattern[str], str]], str]:
    """The policy's own path rules decide what counts as consequential, so the scan reflects your policy."""
    pol, source = _policy_source(cwd)
    if not (pol.get("path_confirm") or []):
        with open(DEFAULT_POLICY_PATH) as fh:
            pol, source = json.load(fh), "the default policy"
    out: list[tuple[re.Pattern[str], str]] = []
    for r in pol.get("path_confirm") or []:
        try:
            out.append((re.compile(r["pattern"]), r.get("reason", "consequential path")))
        except (re.error, KeyError, TypeError):
            continue
    return out, source


def build(cwd: str, since: str = "90d") -> dict[str, Any]:
    if not os.path.isdir(cwd):
        raise ValueError(f"not a git repository: {cwd}")
    try:
        top = toplevel(cwd) or cwd
    except OSError as e:
        raise ValueError(f"not a git repository: {cwd}") from e
    since_date = _since_date(since)
    pats, policy_source = _patterns(top)
    args = [
        "log",
        f"-{MAX_COMMITS}",
        f"--since={since_date}",
        "--no-merges",
        "--name-only",
        "--date=short",
        "--format=%x01%H%x00%ad%x00%s%x00%B%x02",
    ]
    try:
        rc, out, err = git(args, top)
    except OSError as e:
        raise ValueError(f"not a git repository: {cwd}") from e
    if rc != 0:
        raise ValueError(err or f"not a git repository: {cwd}")
    commits = 0
    agent_commits = 0
    agents: dict[str, int] = {}
    consequential: list[dict[str, Any]] = []
    decided = opened = 0
    files: dict[str, int] = {}
    for rec in out.split("\x01"):
        if not rec.strip():
            continue
        head, _, tail = rec.partition("\x02")
        parts = head.split("\x00")
        if len(parts) < 4:
            continue
        sha, date, subject, body = parts[0], parts[1], parts[2], parts[3]
        commits += 1
        seen = {name for name, rx in COMPILED if rx.search(body)}
        if SESSION_RE.search(body):
            seen.add("recorded by gitvow")
        if not seen:
            continue
        agent_commits += 1
        for name in seen:
            agents[name] = agents.get(name, 0) + 1
        changed = [ln.strip() for ln in tail.splitlines() if ln.strip()]
        hits = []
        for f in changed:
            for rx, reason in pats:
                if rx.search(f):
                    hits.append((f, reason))
                    files[f] = files.get(f, 0) + 1
                    break
        if not hits:
            continue
        trailers = parse_trailers(body)
        answered = [t for t in trailers if t["answer"] in ("accepted", "declined")]
        still_open = [t for t in trailers if t["answer"] == "open"]
        if answered:
            decided += 1
        if still_open:
            opened += 1
        consequential.append(
            {
                "sha": sha[:7],
                "date": date,
                "subject": subject[:70],
                "files": [f for f, _ in hits][:6],
                "reasons": sorted({r for _, r in hits}),
                "recorded": bool(answered),
                "open": bool(still_open),
                "by": sorted({t["by"] for t in answered if t.get("by")}),
            }
        )
    return {
        "repo": os.path.basename(top),
        "since": since_date,
        "until": time.strftime("%Y-%m-%d"),
        "window": since,
        "commits": commits,
        "agent_commits": agent_commits,
        "agent_share": round(agent_commits / commits, 2) if commits else None,
        "agents": dict(sorted(agents.items(), key=lambda kv: -kv[1])),
        "consequential": consequential,
        "recorded": decided,
        "open": opened,
        "unrecorded": len(consequential) - decided,
        "files": [{"path": p, "commits": n} for p, n in sorted(files.items(), key=lambda kv: -kv[1])[:8]],
        "policy": policy_source,
    }


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def render(d: dict[str, Any]) -> str:
    lines: list[str] = []
    win = d["window"] if re.fullmatch(r"\d+[dwmy]", d["window"] or "") else f"since {d['since']}"
    human = {"d": "days", "w": "weeks", "m": "months", "y": "years"}
    if re.fullmatch(r"\d+[dwmy]", win):
        win = f"the last {win[:-1]} {human[win[-1]]}"
    lines.append(f"{d['repo']} · {d['commits']} commits in {win}, merges excluded")
    lines.append("")
    if not d["commits"]:
        lines.append("  no commits in this window. Try a longer one: gitvow scan --since 1y")
        return "\n".join(lines) + "\n"
    if not d["agent_commits"]:
        lines.append("  no commits carry an agent's signature in this window.")
        lines.append("")
        lines.append("  Either agents are not writing here yet, or they leave no trailer. gitvow records")
        lines.append("  every session from the moment it is installed, whether the agent signs or not.")
        lines.append("")
        lines.append("  Next: pipx install gitvow && gitvow install --user --check")
        return "\n".join(lines) + "\n"
    pct = round(100 * d["agent_commits"] / d["commits"])
    who = ", ".join(d["agents"]) or "unknown"
    lines.append(f"  {pct:>3}%  agent-assisted{'':11}{d['agent_commits']} of {d['commits']} · {who}")
    n = len(d["consequential"])
    if n:
        shown = ", ".join(d["consequential"][0]["files"][:2]) if d["consequential"] else ""
        more = f", +{len(d['files']) - 2} more" if len(d["files"]) > 2 else ""
        lines.append(f"  {n:>4}  touched a gated file{'':6}{shown}{more}")
        lines.append(f"  {d['recorded']:>4}  recorded who agreed")
    lines.append("")
    if not n:
        lines.append("  None of them touched a file your policy calls consequential, on this history.")
    elif d["recorded"] == 0:
        lines.append("  Nothing is wrong with these commits. Nobody can tell you who agreed to them.")
    elif d["unrecorded"]:
        lines.append(
            f"  {d['recorded']} of {n} record who agreed. The other {d['unrecorded']} "
            f"{_plural(d['unrecorded'], 'does', 'do')} not."
        )
    else:
        lines.append(f"  All {n} record who agreed, with evidence. This is what it looks like when it works.")
    if d["open"]:
        lines.append(f"  {d['open']} {_plural(d['open'], 'carries', 'carry')} an open finding nobody has answered.")
    if n:
        lines.append("")
        lines.append("  Gated files in this window, by the rules in " + d["policy"] + ":")
        for f in d["files"][:5]:
            lines.append(f"    {f['path']}  ({f['commits']} {_plural(f['commits'], 'commit', 'commits')})")
    lines.append("")
    lines.append("  This reads git history and nothing else. It is a floor, not a total: an agent that")
    lines.append("  leaves no trailer is invisible to it, and gitvow does not rely on trailers once installed.")
    lines.append("")
    lines.append("  Next: pipx install gitvow && gitvow install --user --check")
    return "\n".join(lines) + "\n"


def coverage(cwd: str, since: str = "90d") -> dict[str, Any]:
    """Is the record complete? Computed from the remote's own history, trusting no client.

    A commit that carries an agent's signature but no `Gitvow-Session` trailer is a coverage hole: that
    machine was not configured, or its hooks were disabled. This is install health, never a person's
    performance, and it is a floor: an agent that signs nothing at all is invisible here.
    """
    if not os.path.isdir(cwd):
        raise ValueError(f"not a git repository: {cwd}")
    try:
        top = toplevel(cwd) or cwd
    except OSError as e:
        raise ValueError(f"not a git repository: {cwd}") from e
    args = [
        "log",
        f"-{MAX_COMMITS}",
        f"--since={_since_date(since)}",
        "--no-merges",
        "--date=short",
        "--format=%x01%H%x00%ad%x00%an%x00%s%x00%B%x02",
    ]
    try:
        rc, out, err = git(args, top)
    except OSError as e:
        raise ValueError(f"not a git repository: {cwd}") from e
    if rc != 0:
        raise ValueError(err or f"not a git repository: {cwd}")
    signed = 0
    covered = 0
    holes: list[dict[str, Any]] = []
    by_author: dict[str, int] = {}
    for rec in out.split("\x01"):
        if not rec.strip():
            continue
        head, _, _ = rec.partition("\x02")
        parts = head.split("\x00")
        if len(parts) < 5:
            continue
        sha, date, author, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        agents = sorted({name for name, rx in COMPILED if rx.search(body)})
        if not agents:
            continue
        signed += 1
        if SESSION_RE.search(body):
            covered += 1
            continue
        holes.append({"sha": sha[:7], "date": date, "author": author, "subject": subject[:60], "agents": agents})
        by_author[author] = by_author.get(author, 0) + 1
    return {
        "repo": os.path.basename(top),
        "since": _since_date(since),
        "window": since,
        "signed": signed,
        "covered": covered,
        "uncovered": signed - covered,
        "coverage": round(covered / signed, 3) if signed else None,
        "holes": holes[:50],
        "by_author": dict(sorted(by_author.items(), key=lambda kv: -kv[1])),
    }


def render_coverage(d: dict[str, Any], who: bool = False) -> str:
    lines = [f"{d['repo']} · coverage over {_window_words(d['window'])}", ""]
    if not d["signed"]:
        lines.append("  no commits carry an agent's signature in this window, so there is nothing to cover.")
        lines.append("")
        lines.append("  This is a floor, not a total: an agent that signs nothing is invisible to it.")
        return "\n".join(lines) + "\n"
    pct = round(100 * d["coverage"])
    lines.append(f"  {d['signed']:>4}  commits carry an agent's signature")
    lines.append(f"  {d['covered']:>4}  of those are recorded by gitvow{'':10}{pct}%")
    lines.append(f"  {d['uncovered']:>4}  are not")
    lines.append("")
    if not d["uncovered"]:
        lines.append("  Every agent-signed commit in this window carries a session. Nothing to fix.")
    else:
        lines.append("  Uncovered commits, newest first:")
        for h in d["holes"][:12]:
            lines.append(f"    {h['sha']}  {h['date'][5:]}  {h['subject']}  ({', '.join(h['agents'])})")
        if d["uncovered"] > 12:
            lines.append(f"    … and {d['uncovered'] - 12} more")
        if who and d["by_author"]:
            lines.append("")
            lines.append("  Machines to configure, by commit author. Install health, not performance:")
            for a, n in list(d["by_author"].items())[:10]:
                lines.append(f"    {a}  ({n} uncovered)")
        lines.append("")
        lines.append("  Fix: `gitvow install --user --check` on the machine that made these, then `gitvow status`.")
    lines.append("")
    lines.append("  Coverage is a floor. An agent that leaves no signature at all is invisible to it, and")
    lines.append("  a session is recorded from the hook regardless of whether the agent signs its commits.")
    return "\n".join(lines) + "\n"


def _window_words(window: str) -> str:
    human = {"d": "days", "w": "weeks", "m": "months", "y": "years"}
    if re.fullmatch(r"\d+[dwmy]", window or ""):
        return f"the last {window[:-1]} {human[window[-1]]}"
    return f"since {window}"
