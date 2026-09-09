"""Earned rules: findings this repository has answered the same way, by people the policy names, often enough.

A rule is context for the agent, not permission. The gate still asks; the card arrives with the rule attached.
Rules carry their evidence and dates and decay when nobody has confirmed them within the window.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from .decisions import history_all
from .state import toplevel

DEFAULT_THRESHOLD = 3
DEFAULT_DECAY_DAYS = 90
START = "<!-- gitvow:rules -->"
END = "<!-- /gitvow:rules -->"
INSTRUCTION_FILES = {
    "claude": "CLAUDE.md",
    "codex": "AGENTS.md",
    "factory": "AGENTS.md",
    "gemini": "GEMINI.md",
    "copilot": ".github/copilot-instructions.md",
    "cursor": ".cursor/rules/gitvow.mdc",
}


def settings(pol: dict[str, Any]) -> tuple[int, int]:
    d = pol.get("decisions") or {}
    return int(d.get("rule_threshold") or DEFAULT_THRESHOLD), int(d.get("rule_decay_days") or DEFAULT_DECAY_DAYS)


def derive(cwd: str, pol: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    """Rules from the branch's decision history. Only decisions by authorities count; a contradiction resets."""
    top = toplevel(cwd) or cwd
    threshold, decay = settings(pol)
    today_d = _dt.date.fromisoformat(today) if today else _dt.date.today()
    rows = history_all(top, pol=pol)  # newest first, with authority from the note when available
    by_finding: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_finding.setdefault(r["finding"], []).append(r)
    rules, candidates, decayed = [], [], []
    for finding, hist in by_finding.items():
        auth = [h for h in hist if h.get("authority") in ("policy", "commit-access")]
        if not auth:
            continue
        answer = auth[0]["answer"]
        run = []
        for h in auth:  # newest first: count the unbroken run of the latest answer
            if h["answer"] != answer:
                break
            run.append(h)
        dates = sorted(h["date"] for h in run)  # history order is not date order once commits are rebased or backdated
        last = _dt.date.fromisoformat(dates[-1])
        expires = last + _dt.timedelta(days=decay)
        entry = {
            "finding": finding,
            "kind": run[0].get("kind") or _kind(finding),
            "answer": answer,
            "count": len(run),
            "threshold": threshold,
            "first": dates[0],
            "last": dates[-1],
            "by": sorted({h["by"] for h in run if h.get("by")}),
            "scopes": sorted({h["scope"] for h in run if h.get("scope")}),
            "commits": [h["sha"] for h in run[:5]],
            "expires": expires.isoformat(),
            "contradicted_by": len(auth) - len(run),
        }
        if len(run) < threshold:
            candidates.append(entry)
        elif expires < today_d:
            decayed.append(entry)
        else:
            rules.append(entry)
    key = lambda e: (e["last"], e["count"])  # noqa: E731
    return {
        "repo": top,
        "threshold": threshold,
        "decay_days": decay,
        "rules": sorted(rules, key=key, reverse=True),
        "candidates": sorted(candidates, key=key, reverse=True),
        "decayed": sorted(decayed, key=key, reverse=True),
    }


def _kind(finding: str) -> str:
    if finding.startswith("remove route "):
        return "route-removal"
    if finding.startswith("route "):
        return "route"
    if finding.startswith("run "):
        return "command"
    return "edit"


def rule_for(cwd: str, pol: dict[str, Any], finding: str) -> dict[str, Any] | None:
    for r in derive(cwd, pol)["rules"]:
        if r["finding"] == finding:
            return r
    return None


def _sentence(r: dict[str, Any]) -> str:
    who = ", ".join(r["by"][:3]) or "authorities"
    scope = f" for {', '.join(r['scopes'])}" if r["scopes"] else ""
    times = f"{r['count']} time{'s' if r['count'] != 1 else ''}"
    if r["answer"] == "accepted":
        return (
            f"`{r['finding']}` has been accepted {times}{scope} ({r['first']} to {r['last']}, by {who}). "
            "Expect the card to propose accept; still put it to the person."
        )
    return (
        f"`{r['finding']}` has been declined {times} ({r['first']} to {r['last']}, by {who}). "
        "Propose the alternative first; if the change still needs it, say why on the card."
    )


def render(d: dict[str, Any], for_agent: bool = True) -> str:
    """Markdown block for an instruction file or SessionStart context."""
    if not d["rules"]:
        return ""
    lines = [
        START,
        f"## What this repository has decided ({len(d['rules'])} earned rule{'s' if len(d['rules']) != 1 else ''})",
        "",
        "Derived by gitvow from decisions on earlier commits. Context, not permission: the gate still asks at commit.",
        "",
    ]
    for r in d["rules"]:
        lines.append(f"- {_sentence(r)} Decays {r['expires']} unless confirmed again.")
    if not for_agent and d["candidates"]:
        lines += ["", f"Not yet rules (fewer than {d['threshold']} consistent answers by authorities):"]
        for r in d["candidates"]:
            lines.append(
                f"- {r['finding']}: {r['answer']} {r['count']} times (last {r['last']} by {', '.join(r['by'])})"
            )
    if not for_agent and d["decayed"]:
        lines += ["", "Decayed (no confirmation within the window):"]
        for r in d["decayed"]:
            lines.append(
                f"- {r['finding']}: {r['answer']} {r['count']} times, last {r['last']}, expired {r['expires']}"
            )
    lines.append(END)
    return "\n".join(lines) + "\n"


def write_section(path: str, block: str) -> str:
    """Replace or append the managed section in an instruction file; remove it when the block is empty."""
    import os

    existing = ""
    if os.path.exists(path):
        with open(path) as fh:
            existing = fh.read()
    pat = re.compile(re.escape(START) + r".*?" + re.escape(END) + r"\n?", re.S)
    if pat.search(existing):
        new = pat.sub(lambda m: block, existing)
        action = "updated" if block else "removed"
    elif block:
        new = (existing.rstrip("\n") + "\n\n" if existing.strip() else "") + block
        action = "added"
    else:
        return "nothing to write"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(new)
    return f"{action} managed section in {path}"
