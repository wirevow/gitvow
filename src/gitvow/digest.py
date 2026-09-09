"""Period summary from the record: trailers, session notes and the hook log over a time window."""

from __future__ import annotations

import collections
import json
import os
import re
import time
from typing import Any

from .recall import _commits, _plan
from .state import toplevel


def parse_since(text: str) -> str:
    """'7d' / '24h' / 'YYYY-MM-DD' -> a git --since value."""
    m = re.fullmatch(r"(\d+)([dhw])", text or "")
    if m:
        n, unit = int(m.group(1)), m.group(2)
        secs = n * {"d": 86400, "h": 3600, "w": 7 * 86400}[unit]
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - secs))
    return text


def build(cwd: str, since: str = "7d") -> dict[str, Any]:
    top = toplevel(cwd) or cwd
    since_val = parse_since(since)
    since_day = since_val[:10]
    rows = [r for r in _commits(top, None, 5000) if r["date"] >= since_day]  # _commits has no --since
    agent = [r for r in rows if r["kind"] == "agent"]
    human = [r for r in rows if r["kind"] == "person"]
    sessions: dict[str, dict[str, Any]] = {}
    added_total = human_after = 0
    agent_added = 0.0
    files: collections.Counter[str] = collections.Counter()
    file_sessions: dict[str, set[str]] = collections.defaultdict(set)
    for r in agent:
        s = sessions.setdefault(
            r["session"],
            {"session": r["session"], "date": r["date"], "commits": 0, "plan": "", "shares": [], "human_after": 0},
        )
        s["commits"] += 1
        s["date"] = min(s["date"], r["date"])
        note = r["note"] or {}
        s["plan"] = s["plan"] or _plan(note)
        att = note.get("attribution") or {}
        la = att.get("lines_added_in_commit") or 0
        share = att.get("agent_share")
        added_total += la
        if isinstance(share, (int, float)):
            agent_added += la * share
            s["shares"].append(share)
        ha = att.get("lines_changed_by_human_after_agent") or 0
        human_after += ha
        s["human_after"] += ha
        for f in att.get("files") or []:
            if f.get("agent_wrote"):
                files[f["path"]] += 1
                file_sessions[f["path"]].add(r["session"])
    gate: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    log = os.path.join(top, ".git", "gitvow-hooks.log")
    if os.path.exists(log):
        with open(log) as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if (e.get("ts") or "")[:10] < since_day:
                    continue
                if e.get("kind") in ("blocked", "confirm_required"):
                    gate[e["kind"]] += 1
                    reasons[e.get("reason") or "?"] += 1
    sess_list = sorted(sessions.values(), key=lambda s: s["date"], reverse=True)
    for s in sess_list:
        s["share"] = round(sum(s["shares"]) / len(s["shares"]), 2) if s["shares"] else None
        del s["shares"]
    return {
        "repo": os.path.basename(top),
        "since": since_day,
        "until": time.strftime("%Y-%m-%d"),
        "commits": len(rows),
        "agent_commits": len(agent),
        "human_commits": len(human),
        "sessions": sess_list,
        "agent_share": round(agent_added / added_total, 2) if added_total else None,
        "lines_changed_by_human_after_agent": human_after,
        "gate": {
            "confirmations": gate["confirm_required"],
            "denials": gate["blocked"],
            "top_reasons": reasons.most_common(5),
        },
        "files": [{"path": p, "commits": n, "sessions": len(file_sessions[p])} for p, n in files.most_common(10)],
        "human": [{"short": r["short"], "date": r["date"], "subject": r["subject"]} for r in human[:15]],
    }


def render(d: dict[str, Any]) -> str:
    pct = f" ({100 * d['agent_commits'] // d['commits']}%)" if d["commits"] else ""
    out = [f"## gitvow digest · {d['repo']} · {d['since']} → {d['until']}", ""]
    out.append(f"Commits: {d['commits']} · by agents {d['agent_commits']}{pct} · by people {d['human_commits']}")
    share = "n/a" if d["agent_share"] is None else f"{d['agent_share']:.2f}"
    out.append(
        f"Sessions: {len(d['sessions'])} · agent share of added lines {share} · lines changed by people after agents {d['lines_changed_by_human_after_agent']}"
    )
    g = d["gate"]
    top = ", ".join(f"{r} ({n})" for r, n in g["top_reasons"]) or "none"
    out.append(
        f"Gate: {g['confirmations']} confirmation{'s' if g['confirmations'] != 1 else ''} asked, {g['denials']} denial{'s' if g['denials'] != 1 else ''} · top reasons: {top}"
    )
    if d["sessions"]:
        out += ["", "### Sessions"]
        for s in d["sessions"]:
            sh = "n/a " if s["share"] is None else f"{s['share']:.2f}"
            plan = f'  "{s["plan"]}"' if s["plan"] else "  (no plan recorded)"
            after = (
                f"  · {s['human_after']} human edit{'s' if s['human_after'] != 1 else ''} after"
                if s["human_after"]
                else ""
            )
            out.append(
                f"{s['session'][:8]}  {s['date'][5:]}  {s['commits']} commit{'s' if s['commits'] != 1 else ' '}  share {sh}{plan}{after}"
            )
    if d["files"]:
        out += ["", "### Files most changed by agents"]
        for f in d["files"]:
            out.append(
                f"{f['path']}  {f['commits']} commit{'s' if f['commits'] != 1 else ''}, {f['sessions']} session{'s' if f['sessions'] != 1 else ''}"
            )
    if d["human"]:
        out += ["", "### Human commits in the period"]
        for h in d["human"]:
            out.append(f"{h['short']}  {h['date'][5:]}  {h['subject'][:70]}")
    return "\n".join(out) + "\n"
