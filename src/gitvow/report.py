"""Per-commit report for a range: trailers paired with session notes, attribution, and said-vs-did."""

from __future__ import annotations

import json
import os
import re
from typing import Any

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


def build(cwd: str, base: str, head: str = "HEAD") -> dict[str, Any]:
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
            commits.append(entry)
            continue
        sid = m.group(1)
        sm = STEP_RE.search(body)
        entry.update({"kind": "agent", "session_id": sid, "step": int(sm.group(1)) if sm else None})
        note = _note_for(cwd, sha, sid)
        entry["note_found"] = note is not None
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
                }
            )
        commits.append(entry)
    agent = [c for c in commits if c["kind"] == "agent"]
    return {
        "base": base,
        "head": head,
        "commits": commits,
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
    for c in r["commits"]:
        if c["kind"] == "human":
            lines += [f"### {c['short']} {c['subject']} — no session trailer (made by a person)", ""]
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
        lines.append(f"**Tools:** {tools} · {c['tool_calls']} tool calls · {c['turns']} turns")
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
        lines += ["**Said vs did:** " + " · ".join(parts), ""]
    return "\n".join(lines).rstrip() + "\n"
