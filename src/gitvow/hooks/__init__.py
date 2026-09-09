"""Hook handlers. Each takes the Claude Code hook payload (dict) and returns (exit_code, stderr_message)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .. import snapshots
from ..policy import PolicyError, evaluate, load_policy, message_for
from ..pricing import estimate
from ..redact import RedactionError, load_rules, redact
from ..state import git, load_state, log_event, save_state, toplevel
from ..transcript import summarize

NOTES_REF_PREFIX = "gitvow"  # refs/notes/gitvow/<session-id>; gitvow 0.1 wrote the single ref refs/notes/sessions
LEGACY_NOTES_REF = "sessions"
NOTE_SCHEMA = 4
COMMIT_RE = re.compile(r"\bgit\s+commit\b")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
REDACTION_UNAVAILABLE = "redaction rules invalid; nothing written for this event"


def notes_ref(session_id: str | None) -> str:
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")
    return f"{NOTES_REF_PREFIX}/{sid}"


def session_start(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    st = load_state(cwd)
    same_session = st.get("session_id") == h.get("session_id")  # a resumed session keeps its counters
    st.update(
        {
            "session_id": h.get("session_id"),
            "transcript_path": h.get("transcript_path"),
            "started": st.get("started") if same_session and st.get("started") else time.strftime("%Y-%m-%dT%H:%M:%S"),
            "steps": st.get("steps", 0) if same_session else 0,
            "agent_blobs": st.get("agent_blobs", {}) if same_session else {},
            "snapshots": st.get("snapshots", 0) if same_session else 0,
            "last_snapshot": st.get("last_snapshot") if same_session else None,
        }
    )
    st.pop("pending_commit", None)
    save_state(cwd, st)
    log_event(cwd, "session_start", {"session_id": h.get("session_id")})
    return 0, ""


def _policy_or_empty(cwd: str, home: str | None) -> dict[str, Any]:
    try:
        return load_policy(cwd, home)
    except PolicyError:
        return {}


def subagent_event(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    """Record a subagent lifecycle event from agents that announce them through hooks (Cursor)."""
    cwd = h.get("cwd") or os.getcwd()
    log_event(
        cwd,
        "subagent",
        {
            "session_id": h.get("session_id"),
            "status": h.get("status", ""),
            "type": h.get("subagent_type", ""),
            "tool_calls": h.get("tool_call_count", 0),
        },
    )
    return 0, ""


def _rules_or_none(cwd: str, home: str | None) -> tuple[list[tuple[str, str]] | None, str]:
    try:
        return load_rules(cwd, home), ""
    except RedactionError as e:
        log_event(cwd, "redaction_unavailable", {"error": str(e)[:200]})
        return None, f"gitvow: {e}. {REDACTION_UNAVAILABLE}."


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
    d = evaluate(pol, tool, inp, cwd)
    rules, warn = _rules_or_none(cwd, home)
    if d.blocks:
        payload: dict[str, Any] = {"tool": tool, "reason": d.reason, "session_id": h.get("session_id")}
        if rules is not None:
            payload["detail"] = redact(d.detail, rules)[:200]
        log_event(cwd, "blocked" if d.outcome == "deny" else "confirm_required", payload)
        return 2, message_for(d)
    if tool == "Bash" and COMMIT_RE.search(inp.get("command", "")):
        st = load_state(cwd)
        st["session_id"] = h.get("session_id") or st.get("session_id")
        st["transcript_path"] = h.get("transcript_path") or st.get("transcript_path")
        st["steps"] = st.get("steps", 0) + 1
        st["pending_commit"] = time.time()  # the git hook adds trailers only while this is fresh
        save_state(cwd, st)
    if rules is None:
        log_event(cwd, "allowed", {"tool": tool})
        return 0, warn
    log_event(
        cwd,
        "allowed",
        {"tool": tool, "detail": redact(inp.get("command") or inp.get("file_path") or tool, rules)[:160]},
    )
    return 0, ""


def _record_agent_blob(cwd: str, file_path: str) -> None:
    """After an agent edit, remember the blob id of what the agent wrote (repo-relative path -> blob)."""
    top = toplevel(cwd)
    if not top:
        return
    abs_path = file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
    if not os.path.isfile(abs_path):
        return
    rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(top))
    if rel.startswith(".."):
        return
    rc, blob, _ = git(["hash-object", "-w", "--", abs_path], top)
    if rc != 0:
        return
    st = load_state(cwd)
    st.setdefault("agent_blobs", {})[rel] = blob
    save_state(cwd, st)


def _attribution(cwd: str, head: str, agent_blobs: dict[str, str], transcript_written: list[str]) -> dict[str, Any]:
    _, numstat, _ = git(["show", "--numstat", "--format=", head], cwd)
    files: list[dict[str, Any]] = []
    total_added = human_changed = agent_added = 0
    for ln in numstat.splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        added = int(parts[0]) if parts[0].isdigit() else 0
        path = parts[2]
        total_added += added
        agent_blob = agent_blobs.get(path)
        touched = agent_blob is not None or any(path.endswith(w) or w.endswith(path) for w in transcript_written)
        entry: dict[str, Any] = {"path": path, "agent_wrote": touched, "lines_added_in_commit": added}
        if agent_blob:
            rc, committed_out, _ = git(["rev-parse", f"{head}:{path}"], cwd)
            committed: str | None = committed_out if rc == 0 else None
            h_add = h_del = 0
            if committed and committed != agent_blob:
                rc, d, _ = git(["diff", "--numstat", agent_blob, committed], cwd)
                if rc == 0 and d:
                    a, b = d.split("\t")[:2]
                    h_add, h_del = (int(a) if a.isdigit() else 0), (int(b) if b.isdigit() else 0)
            human_changed += h_add + h_del
            agent_added += max(added - h_add, 0)
            entry.update(
                {
                    "human_lines_added": h_add,
                    "human_lines_removed": h_del,
                    "agent_blob": agent_blob,
                    "committed_blob": committed,
                }
            )
        files.append(entry)
    have_blobs = any("agent_blob" in f for f in files)
    return {
        "files_in_commit": len(files),
        "touched_by_agent": sum(1 for f in files if f["agent_wrote"]),
        "lines_added_in_commit": total_added,
        "lines_changed_by_human_after_agent": human_changed,
        # None when nothing can be attributed: no lines added, or no agent-written blob was recorded
        "agent_share": round(agent_added / total_added, 2) if total_added and have_blobs else None,
        "files": files[:50],
    }


def post_tool_use(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    tool = h.get("tool_name")
    inp = h.get("tool_input") or {}
    if tool in EDIT_TOOLS:
        fp = str(inp.get("file_path") or inp.get("notebook_path") or "")
        if fp:
            _record_agent_blob(cwd, fp)
        try:
            pol = load_policy(cwd, home)
        except PolicyError:
            pol = {}
        cfg = snapshots.settings(pol)
        if cfg.get("enabled", True):
            ref = snapshots.take(cwd, h.get("session_id") or load_state(cwd).get("session_id"), tool, fp, cfg)
            if ref:
                log_event(cwd, "snapshot", {"ref": ref, "tool": tool})
        return 0, ""
    if tool != "Bash" or not COMMIT_RE.search(inp.get("command", "")):
        return 0, ""
    st = load_state(cwd)
    if "pending_commit" in st:
        del st["pending_commit"]
        save_state(cwd, st)
    rc, head, _ = git(["rev-parse", "HEAD"], cwd)
    if rc != 0:
        return 0, ""
    rules, warn = _rules_or_none(cwd, home)
    if rules is None:
        return 0, warn
    summ = summarize(h.get("transcript_path") or st.get("transcript_path"), rules=rules)
    _, head_msg, _ = git(["log", "-1", "--format=%B", head], cwd)
    if "Gitvow-Session:" not in head_msg:
        return 0, ""  # the commit did not go through (or was not the agent's): no note
    _, files, _ = git(["show", "--stat", "--format=", head], cwd)
    attribution = _attribution(cwd, head, st.get("agent_blobs", {}), summ["files_written"])
    session_id = h.get("session_id") or st.get("session_id")
    note = {
        "schema": NOTE_SCHEMA,
        "session_id": session_id,
        "step": st.get("steps"),
        "committed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "assistant_turns_so_far": summ["turns"],
        "tool_calls_so_far": len(summ["tool_calls"]),
        "tools_used": sorted({t["tool"] for t in summ["tool_calls"] if t.get("tool")}),
        "last_stated_plan": summ["last_assistant_text"],
        "files_in_commit": [ln.strip() for ln in files.splitlines()[:-1]][:50] if files else [],
        "files_written_by_agent_this_session": [f["path"] for f in attribution["files"] if f["agent_wrote"]][:50],
        "attribution": attribution,
        "usage": estimate(summ["usage"], _policy_or_empty(cwd, home)),
        "subagents": summ["subagents"],
        "snapshot": st.get("last_snapshot"),
        "transcript": "kept local; see ledger",
        "redaction": "secrets/PII patterns, high-entropy tokens and custom rules replaced at write time",
    }
    ref = notes_ref(session_id)
    body = "gitvow-session\n" + json.dumps(note, indent=1)
    git(["notes", f"--ref={ref}", "add", "-f", "-m", body, head], cwd)
    log_event(cwd, "note_added", {"commit": head[:12], "session_id": session_id, "step": note["step"]})
    return 0, f"session note attached to {head[:12]} (refs/notes/{ref})"


def stop(h: dict[str, Any], home: str | None = None) -> tuple[int, str]:
    cwd = h.get("cwd") or os.getcwd()
    home = home or os.path.expanduser("~")
    rules, warn = _rules_or_none(cwd, home)
    if rules is None:
        return 0, warn
    st = load_state(cwd)
    summ = summarize(h.get("transcript_path") or st.get("transcript_path"), rules=rules)
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
        "usage": estimate(summ["usage"], _policy_or_empty(cwd, home)),
        "subagents": summ["subagents"],
    }
    with open(os.path.join(led, f"{h.get('session_id') or 'unknown'}.json"), "w") as fh:
        json.dump(rec, fh, indent=1)
    log_event(cwd, "session_stop", {"session_id": h.get("session_id"), "tool_calls": len(summ["tool_calls"])})
    return 0, ""


HANDLERS = {
    "SessionStart": session_start,
    "PreToolUse": pre_tool_use,
    "PostToolUse": post_tool_use,
    "Stop": stop,
    "Subagent": subagent_event,
}
