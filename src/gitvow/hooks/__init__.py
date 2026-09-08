"""Hook handlers. Each takes the Claude Code hook payload (dict) and returns (exit_code, stderr_message)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from ..policy import PolicyError, evaluate, load_policy, message_for
from ..redact import redact
from ..state import git, load_state, log_event, save_state
from ..transcript import summarize

NOTES_REF = "sessions"
COMMIT_RE = re.compile(r"\bgit\s+commit\b")


def session_start(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    st = load_state(cwd)
    st.update(
        {
            "session_id": h.get("session_id"),
            "transcript_path": h.get("transcript_path"),
            "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "steps": st.get("steps", 0),
        }
    )
    save_state(cwd, st)
    log_event(cwd, "session_start", {"session_id": h.get("session_id")})
    return 0, ""


def pre_tool_use(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    tool = h.get("tool_name", "") or ""
    inp = h.get("tool_input") or {}
    try:
        pol = load_policy(cwd, home)
    except PolicyError as e:
        return (
            2,
            f"BLOCKED: tool policy could not be loaded ({e}); refusing all tool calls until the policy is restored.",
        )
    d = evaluate(pol, tool, inp)
    if d.blocks:
        log_event(
            cwd,
            "blocked" if d.outcome == "deny" else "confirm_required",
            {"tool": tool, "reason": d.reason, "detail": redact(d.detail)[:200], "session_id": h.get("session_id")},
        )
        return 2, message_for(d)
    if tool == "Bash" and COMMIT_RE.search(inp.get("command", "")):
        st = load_state(cwd)
        st["session_id"] = h.get("session_id") or st.get("session_id")
        st["transcript_path"] = h.get("transcript_path") or st.get("transcript_path")
        st["steps"] = st.get("steps", 0) + 1
        save_state(cwd, st)
    log_event(
        cwd, "allowed", {"tool": tool, "detail": redact(inp.get("command") or inp.get("file_path") or tool)[:160]}
    )
    return 0, ""


def post_tool_use(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    if h.get("tool_name") != "Bash" or not COMMIT_RE.search((h.get("tool_input") or {}).get("command", "")):
        return 0, ""
    rc, head, _ = git(["rev-parse", "HEAD"], cwd)
    if rc != 0:
        return 0, ""
    st = load_state(cwd)
    summ = summarize(h.get("transcript_path") or st.get("transcript_path"))
    _, files, _ = git(["show", "--stat", "--format=", "HEAD"], cwd)
    _, diffstat, _ = git(["show", "--numstat", "--format=", "HEAD"], cwd)
    changed = [ln.split("\t")[-1] for ln in diffstat.splitlines() if ln.strip()]
    agent_written = sorted(f for f in changed if any(f.endswith(w) or w.endswith(f) for w in summ["files_written"]))
    note = {
        "session_id": h.get("session_id") or st.get("session_id"),
        "step": st.get("steps"),
        "committed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assistant_turns_so_far": summ["turns"],
        "tool_calls_so_far": len(summ["tool_calls"]),
        "tools_used": sorted({t["tool"] for t in summ["tool_calls"] if t.get("tool")}),
        "last_stated_plan": summ["last_assistant_text"],
        "files_in_commit": [ln.strip() for ln in files.splitlines()[:-1]][:50] if files else [],
        "files_written_by_agent_this_session": agent_written[:50],
        "attribution": {"files_in_commit": len(changed), "touched_by_agent": len(agent_written)},
        "transcript": "kept local; see ledger",
        "redaction": "secrets/PII patterns and high-entropy tokens replaced at write time",
    }
    body = "gitvow-session\n" + json.dumps(note, indent=1)
    git(["notes", f"--ref={NOTES_REF}", "add", "-f", "-m", body, head], cwd)
    log_event(cwd, "note_added", {"commit": head[:12], "session_id": note["session_id"], "step": note["step"]})
    return 0, f"session note attached to {head[:12]} (refs/notes/{NOTES_REF})"


def stop(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    home = home or os.path.expanduser("~")
    st = load_state(cwd)
    summ = summarize(h.get("transcript_path") or st.get("transcript_path"))
    led = os.path.join(home, ".gitvow", "ledger")
    os.makedirs(led, exist_ok=True)
    commits: list[str] = []
    if st.get("started"):
        rc, out, _ = git(["log", "--format=%H", f"--since={st['started']}"], cwd)
        commits = out.split() if rc == 0 else []
    rec = {
        "session_id": h.get("session_id"),
        "repo": cwd,
        "started": st.get("started"),
        "ended": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assistant_turns": summ["turns"],
        "tool_calls": summ["tool_calls"],
        "commits_during_session": commits,
        "last_stated_plan": summ["last_assistant_text"],
    }
    with open(os.path.join(led, f"{h.get('session_id') or 'unknown'}.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    log_event(cwd, "session_stop", {"session_id": h.get("session_id"), "tool_calls": len(summ["tool_calls"])})
    return 0, ""


HANDLERS = {"SessionStart": session_start, "PreToolUse": pre_tool_use, "PostToolUse": post_tool_use, "Stop": stop}
