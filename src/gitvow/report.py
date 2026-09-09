"""Per-commit report for a range: trailers paired with session notes, attribution, and said-vs-did."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .decisions import parse_trailers
from .hooks import LEGACY_NOTES_REF, notes_ref
from .state import git

TRAILER_RE = re.compile(r"^Gitvow-Session:\s*(\S+)", re.M)
STEP_RE = re.compile(r"^Gitvow-Step:\s*(\d+)", re.M)


def _note_for(cwd: str, sha: str, session_id: str) -> dict[str, Any] | None:
    for ref in (notes_ref(session_id), LEGACY_NOTES_REF):
        rc, body, _ = git(["notes", f"--ref={ref}", "show", sha], cwd)
        if rc == 0 and body.startswith("gitvow-session"):
            try:
                return json.loads(body.split("\n", 1)[1])
            except (json.JSONDecodeError, IndexError):
                return None
    return None


def _changed_files(cwd: str, sha: str) -> list[str]:
    _, out, _ = git(["show", "--name-only", "--format=", sha], cwd)
    return [ln for ln in out.splitlines() if ln.strip()]


def said_vs_did(plan: str, files: list[str]) -> dict[str, list[str]]:
    """Which changed files does the stated plan mention (by path or basename, case-insensitive)?"""
    low = plan.lower()
    mentioned, unmentioned = [], []
    for f in files:
        base = os.path.basename(f).lower()
        stem = os.path.splitext(base)[0]
        if f.lower() in low or base in low or (len(stem) > 3 and stem in low):
            mentioned.append(f)
        else:
            unmentioned.append(f)
    return {"mentioned": mentioned, "unmentioned": unmentioned}


DEFAULT_PRODUCTION = ("main", "master", "production")


def _decisions(
    body: str, note: dict[str, Any] | None, target: str | None, production: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Decisions a commit carries: trailers, enriched from the note, with scope reopened at a production branch."""
    rows = parse_trailers(body)
    by_finding = {d.get("finding"): d for d in (note or {}).get("decisions") or []}
    tgt = (target or "").split("/")[-1] if target else ""
    for r in rows:
        n = by_finding.get(r["finding"]) or {}
        r["authority"] = n.get("authority")
        r["evidence"] = n.get("evidence") or []
        r["human_turns_after_card"] = n.get("human_turns_after_card")
        wanted = {"accept": "accepted", "decline": "declined"}.get(n.get("proposed") or "")
        r["pre_answered"] = bool(wanted) and wanted == r["answer"]
        r["reopen"] = bool(
            r.get("scope")
            and tgt
            and tgt in production
            and r["scope"].split("/")[-1] != tgt
            and r["answer"] == "accepted"
        )
    return rows


def build(cwd: str, base: str, head: str = "HEAD", target: str | None = None) -> dict[str, Any]:
    from .policy import PolicyError, load_policy

    production: tuple[str, ...] = DEFAULT_PRODUCTION
    try:
        production = tuple((load_policy(cwd).get("decisions") or {}).get("production_branches") or DEFAULT_PRODUCTION)
    except PolicyError:
        production = DEFAULT_PRODUCTION  # the report must render without a valid policy
    rc, out, err = git(["log", "--reverse", "--format=%H%x00%s%x00%B%x01", f"{base}..{head}"], cwd)
    if rc != 0:
        raise ValueError(err or f"cannot list {base}..{head}")
    commits: list[dict[str, Any]] = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, subject, body = rec.split("\x00", 2)
        m = TRAILER_RE.search(body)
        entry: dict[str, Any] = {"sha": sha, "short": sha[:7], "subject": subject, "files": _changed_files(cwd, sha)}
        if not m:
            entry["kind"] = "human"
            entry["decisions"] = _decisions(body, None, target, production)
            commits.append(entry)
            continue
        sid = m.group(1)
        sm = STEP_RE.search(body)
        entry.update({"kind": "agent", "session_id": sid, "step": int(sm.group(1)) if sm else None})
        note = _note_for(cwd, sha, sid)
        entry["note_found"] = note is not None
        entry["decisions"] = _decisions(body, note, target, production)
        if note:
            plan = note.get("last_stated_plan") or ""
            att = note.get("attribution") or {}
            entry.update(
                {
                    "plan": plan,
                    "tools_used": note.get("tools_used", []),
                    "tool_calls": note.get("tool_calls_so_far"),
                    "turns": note.get("assistant_turns_so_far"),
                    "attribution": {
                        "files_in_commit": att.get("files_in_commit"),
                        "touched_by_agent": att.get("touched_by_agent"),
                        "agent_share": att.get("agent_share"),
                        "lines_changed_by_human_after_agent": att.get("lines_changed_by_human_after_agent"),
                    },
                    "said_vs_did": said_vs_did(plan, entry["files"]),
                    "usage": {
                        "total_tokens": (note.get("usage") or {}).get("total_tokens"),
                        "estimated_cost_usd": (note.get("usage") or {}).get("estimated_cost_usd"),
                    },
                }
            )
        commits.append(entry)
    agent = [c for c in commits if c["kind"] == "agent"]
    alld = [d for c in commits for d in c.get("decisions", [])]
    return {
        "base": base,
        "head": head,
        "target": target,
        "commits": commits,
        "decisions": {
            "accepted": sum(1 for d in alld if d["answer"] == "accepted"),
            "declined": sum(1 for d in alld if d["answer"] == "declined"),
            "open": sum(1 for d in alld if d["answer"] == "open"),
            "reopened": sum(1 for d in alld if d.get("reopen")),
            "pre_answered": sum(1 for d in alld if d.get("pre_answered")),
        },
        "agent_commits": len(agent),
        "human_commits": len(commits) - len(agent),
        "missing_notes": [c["short"] for c in agent if not c["note_found"]],
    }


def render_markdown(r: dict[str, Any]) -> str:
    lines = [
        f"## gitvow: {r['agent_commits']} agent commit{'s' if r['agent_commits'] != 1 else ''}, "
        f"{r['human_commits']} human commit{'s' if r['human_commits'] != 1 else ''}",
        "",
    ]
    if r["missing_notes"]:
        lines += [
            f"> **Missing session notes** for {', '.join(r['missing_notes'])}. "
            "The author's notes were not pushed; run `gitvow push-notes` or `git push origin 'refs/notes/gitvow/*'`.",
            "",
        ]
    ds = r.get("decisions") or {}
    if any(ds.values()):
        bits = [f"{ds['accepted']} accepted", f"{ds['declined']} declined"]
        if ds.get("open"):
            bits.append(f"**{ds['open']} open**")
        if ds.get("reopened"):
            bits.append(f"**{ds['reopened']} to reopen for {r.get('target')}**")
        if ds.get("pre_answered"):
            bits.append(f"{ds['pre_answered']} matched the record's proposal")
        lines += [f"**Decisions:** {' · '.join(bits)}", ""]
    for c in r["commits"]:
        if c["kind"] == "human":
            lines += [f"### {c['short']} {c['subject']} — no session trailer (made by a person)"]
            lines += _decision_lines(c.get("decisions") or [], r.get("target"))
            lines.append("")
            continue
        sid = c["session_id"][:8]
        lines.append(
            f"### {c['short']} {c['subject']} — session {sid}, step {c['step'] if c['step'] is not None else '?'}"
        )
        if not c["note_found"]:
            lines += ["**Session note:** missing (not pushed)", ""]
            continue
        plan = c["plan"].strip().replace("\n", " ")
        lines.append(f"**Plan:** {plan[:400] if plan else '(none stated before committing)'}")
        tools = ", ".join(c["tools_used"]) or "none recorded"
        u = c.get("usage") or {}
        cost = f" · ${u['estimated_cost_usd']:.2f} est." if u.get("estimated_cost_usd") is not None else ""
        toks = f" · {u['total_tokens'] / 1e3:.0f}k tokens so far" if u.get("total_tokens") else ""
        lines.append(f"**Tools:** {tools} · {c['tool_calls']} tool calls · {c['turns']} turns{toks}{cost}")
        a = c["attribution"]
        share = "n/a" if a["agent_share"] is None else f"{a['agent_share']:.2f}"
        lines.append(
            f"**Attribution:** {a['files_in_commit']} file{'s' if a['files_in_commit'] != 1 else ''}, "
            f"agent share {share}, {a['lines_changed_by_human_after_agent'] or 0} lines changed by a human after the agent"
        )
        svd = c["said_vs_did"]
        parts = []
        if svd["mentioned"]:
            parts.append(", ".join(svd["mentioned"]) + " mentioned in plan ✓")
        if svd["unmentioned"]:
            parts.append(", ".join(svd["unmentioned"]) + " **not mentioned in plan**")
        if not plan:
            parts.append("no plan to compare")
        lines.append("**Said vs did:** " + " · ".join(parts))
        lines += _decision_lines(c.get("decisions") or [], r.get("target"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _decision_lines(ds: list[dict[str, Any]], target: str | None) -> list[str]:
    out = []
    for d in ds:
        if d["answer"] == "open":
            out.append(f"**Open:** {d['finding']} — nobody decided; `gitvow decide` closes it")
            continue
        who = d.get("by") or "unknown"
        auth = f" ({d['authority']})" if d.get("authority") in ("none",) else ""
        scope = f", scope {d['scope']}" if d.get("scope") else ""
        note = f": {d['note']}" if d.get("note") else ""
        line = f"**{d['answer'].capitalize()}:** {d['finding']} by {who}{auth}{scope}{note}"
        if d.get("pre_answered"):
            line += " · matched the record's proposal"
        if d.get("human_turns_after_card") == 0:
            line += " · **answered without a user message in the transcript**"
        if d.get("reopen"):
            line += f" · **accepted for {d['scope']}; this pull request targets {target}. Accept for {target}?**"
        out.append(line)
    return out


def decisions_summary(r: dict[str, Any]) -> str:
    """Trailer block for a pull request description, so a squash commit inherits the decisions."""
    seen: list[str] = []
    for c in r["commits"]:
        for d in c.get("decisions") or []:
            key = {"accepted": "Gitvow-Accepted", "declined": "Gitvow-Declined", "open": "Gitvow-Open"}[d["answer"]]
            line = f"{key}: {d['finding']}"
            if d["answer"] != "open":
                line += f" by {d.get('by') or 'unknown'}"
                if d.get("scope"):
                    line += f" scope={d['scope']}"
                if d.get("note"):
                    line += f": {d['note']}"
            if line not in seen:
                seen.append(line)
    if not seen:
        return ""
    return "<!-- gitvow-decisions -->\n" + "\n".join(seen) + "\n<!-- /gitvow-decisions -->\n"
