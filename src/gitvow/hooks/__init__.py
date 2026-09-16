"""Hook handlers. Each takes the Claude Code hook payload (dict) and returns (exit_code, stderr_message)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .. import decisions as dec
from .. import snapshots
from ..policy import PolicyError, confirm_message, evaluate, load_policy, message_for
from ..pricing import estimate
from ..redact import RedactionError, load_rules, redact
from ..state import git, load_state, log_event, save_state, toplevel
from ..transcript import summarize

NOTES_REF_PREFIX = "gitvow"  # refs/notes/gitvow/<session-id>; gitvow 0.1 wrote the single ref refs/notes/sessions
LEGACY_NOTES_REF = "sessions"
NOTE_SCHEMA = 7  # 7 (0.18): decisions[].answer may be "observed"; note gains edits_outside_repository. 6 added `to` to decisions[]: a referral may name who the question should have gone to
# `git commit`, including the global options that may sit between the two words. `git -c k=v commit` and
# `git -C dir commit` are the same act and used to slip past a `\bgit\s+commit\b` match entirely, which made
# the card trivially avoidable by anyone who knew it. Options are enumerated rather than matched loosely so
# that plumbing with a similar name (`git commit-tree`, `git commit-graph`) still does not match.
_GIT_GLOBAL_VALUED = r"-[cC]|--git-dir|--work-tree|--namespace|--exec-path|--config-env|--super-prefix"
_GIT_GLOBAL_BARE = (
    r"--no-pager|--paginate|-p|-P|--bare|--literal-pathspecs|--no-replace-objects|--no-optional-locks|--glob-pathspecs"
)
COMMIT_RE = re.compile(
    rf"\bgit\b(?:\s+(?:(?:{_GIT_GLOBAL_VALUED})(?:=\S+|\s+\S+)|(?:{_GIT_GLOBAL_BARE})))*\s+commit(?![\w-])"
)
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
            "edits_elsewhere": st.get("edits_elsewhere", {}) if same_session else {},
            "session_answers": st.get("session_answers", {}) if same_session else {},
            "snapshots": st.get("snapshots", 0) if same_session else 0,
            "last_snapshot": st.get("last_snapshot") if same_session else None,
        }
    )
    st.pop("pending_commit", None)
    st.pop("card_ack", None)
    save_state(cwd, st)
    log_event(cwd, "session_start", {"session_id": h.get("session_id")})
    return 0, _rules_context(cwd, home)


def _rules_context(cwd: str, home: str | None) -> str:
    """Earned rules as context for the agent; SessionStart stdout reaches the conversation."""
    from ..rules import derive, render

    try:
        pol = load_policy(cwd, home)
    except PolicyError:
        return ""
    if not toplevel(cwd):
        return ""
    return render(derive(cwd, pol))


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
        if d.outcome == "deny" or not d.findings:
            log_event(cwd, "blocked" if d.outcome == "deny" else "confirm_required", payload)
            return 2, message_for(d)
        # An immediate confirm is a finding too. It is recorded so a person can answer it once for the
        # session; after that the same question is not asked again until the session ends, and the answer
        # rides on the next commit as a scoped decision (scope=session), which is an exception, never precedent.
        session_scope = (pol.get("decisions") or {}).get("session_scope", True)
        f = dict(d.findings[0], immediate=True)
        text = redact(f["finding"], rules) if rules is not None else f["finding"]
        prior = dec.session_answer(cwd, text)
        if session_scope and prior and prior.get("answer") == "accepted" and prior.get("scope") == "session":
            log_event(cwd, "allowed_by_session_answer", {**payload, "by": prior.get("by")})
            return 0, ""
        if prior and prior.get("answer") == "declined":
            log_event(cwd, "confirm_required", {**payload, "declined_by": prior.get("by")})
            return 2, f"BLOCKED: {text} was declined by {prior.get('by')} this session ({d.reason})."
        st = load_state(cwd)
        dec.add(cwd, [f], st.get("steps", 0) + 1, tool, rules)
        n = next((x["n"] for x in dec.open_findings(cwd) if x["finding"] == text), None)
        log_event(cwd, "confirm_required", {**payload, "finding": n})
        return 2, confirm_message(d, n, session_scope)
    if d.deferred or d.observed:
        st = load_state(cwd)
        new = dec.add(cwd, list(d.findings), st.get("steps", 0) + 1, tool, rules)
        log_event(
            cwd,
            "observed" if d.observed else "finding",
            {"tool": tool, "reason": d.reason[:200], "new": new, "session_id": h.get("session_id")},
        )
    if tool == "Bash" and COMMIT_RE.search(inp.get("command", "")):
        pending = dec.undecided(cwd)
        mode = (pol.get("decisions") or {}).get("mode", "open")
        st = load_state(cwd)
        # Open mode: the card is shown once per set of findings. A second commit attempt with nothing new
        # pending means the person has seen it and chose not to answer; the commit goes through and the
        # prepare-commit-msg hook records each unanswered finding as Gitvow-Open. Strict mode never takes
        # this branch, so the card keeps refusing until every finding has an answer.
        acked = set(st.get("card_ack") or [])
        if pending and mode == "open" and all(f["finding"] in acked for f in pending):
            log_event(cwd, "card_ack", {"findings": len(pending), "session_id": h.get("session_id")})
            pending = []
        if pending:
            tp = h.get("transcript_path") or st.get("transcript_path")
            if rules is not None and tp and st.get("card_user_turns") is None:
                st["card_user_turns"] = summarize(tp, rules=rules)["user_turns"]
                st["card_shown_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            if mode == "open":
                st["card_ack"] = [f["finding"] for f in pending]
                # An agent with its own approve button runs the commit the moment the person clicks yes, with
                # no second pass through this gate; mark the commit as the agent's so it still gets its trailers.
                st["pending_commit"] = time.time()
            save_state(cwd, st)
            proposed = dec.mark_proposals(cwd, pol)
            log_event(
                cwd,
                "card",
                {"findings": len(pending), "proposed": proposed, "mode": mode, "session_id": h.get("session_id")},
            )
            return 2, dec.card(cwd, pol=pol, mode=mode)
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


def _record_prompt_approval(cwd: str, inp: dict[str, Any], home: str | None) -> None:
    """A command an immediate-confirm rule stops has just *run*: the agent's own prompt approved it.

    On agents with a native approve button the confirm is delivered as a question, and a click on yes runs the
    command with nothing recorded. PostToolUse fires only for calls that executed, so reaching here for such a
    command means a person approved it. Record that as the session's answer, so the record says what happened and
    the same question is not asked again this session. Nothing to do if `gitvow decide` already recorded one.
    """
    try:
        pol = load_policy(cwd, home)
    except PolicyError:
        return
    if not (pol.get("decisions") or {}).get("session_scope", True):
        return
    d = evaluate(pol, "Bash", inp, cwd)
    if not (d.blocks and d.outcome == "confirm" and d.findings):
        return
    rules, _ = _rules_or_none(cwd, home)
    text = redact(d.findings[0]["finding"], rules) if rules is not None else d.findings[0]["finding"]
    if dec.session_answer(cwd, text):
        return
    st = load_state(cwd)
    if not any(f["finding"] == text for f in st.get("findings") or []):
        dec.add(cwd, [dict(d.findings[0], immediate=True)], st.get("steps", 0) + 1, "Bash", rules)
    n = next((x["n"] for x in dec.open_findings(cwd) if x["finding"] == text), None)
    if n is None:
        return
    dec.decide(cwd, str(n), "accept", pol, scope="session", reason="approved at the agent's prompt", rules=rules)
    log_event(cwd, "prompt_approval_recorded", {"finding": n, "reason": d.reason[:120]})


def _record_agent_blob(cwd: str, file_path: str) -> None:
    """After an agent edit, remember the blob id of what the agent wrote (repo-relative path -> blob).

    An edit outside this repository's tree is counted under the repository it did land in, because the replay
    of three engineers' sessions found 28% of edits going to a different checkout than the session's own. The
    gate still evaluated the path; the record needs to say where the file actually lives.
    """
    top = toplevel(cwd)
    if not top:
        return
    abs_path = file_path if os.path.isabs(file_path) else os.path.join(cwd, file_path)
    if not os.path.isfile(abs_path):
        return
    rel = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(top))
    if rel.startswith(".."):
        other = toplevel(os.path.dirname(abs_path))
        if other and os.path.realpath(other) != os.path.realpath(top):
            st = load_state(cwd)
            key = os.path.basename(other.rstrip("/")) or other
            ent = st.setdefault("edits_elsewhere", {}).setdefault(key, {"count": 0, "paths": []})
            ent["count"] += 1
            p = os.path.relpath(os.path.realpath(abs_path), os.path.realpath(other))
            if p not in ent["paths"] and len(ent["paths"]) < 20:
                ent["paths"].append(p)
            save_state(cwd, st)
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
    if tool == "Bash" and not COMMIT_RE.search(inp.get("command", "")):
        _record_prompt_approval(cwd, inp, home)
        return 0, ""
    if tool != "Bash":
        return 0, ""
    st = load_state(cwd)
    if "pending_commit" in st or "card_ack" in st:
        st.pop("pending_commit", None)
        st.pop("card_ack", None)  # the commit happened; the next set of findings earns its own card
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
    committed = dec.take_committed(cwd, head)
    st = load_state(cwd)
    card_turns = st.pop("card_user_turns", None)
    st.pop("card_shown_at", None)
    save_state(cwd, st)
    decisions = dec.note_entries(committed, card_turns) if committed else []
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
        "decisions": decisions,
        "edits_outside_repository": st.get("edits_elsewhere") or {},
        "transcript": "kept local; see ledger",
        "redaction": "secrets/PII patterns, high-entropy tokens and custom rules replaced at write time",
    }
    ref = notes_ref(session_id)
    body = "gitvow-session\n" + json.dumps(note, indent=1)
    git(["notes", f"--ref={ref}", "add", "-f", "-m", body, head], cwd)
    log_event(
        cwd,
        "note_added",
        {"commit": head[:12], "session_id": session_id, "step": note["step"], "decisions": len(decisions)},
    )
    extra = f", {len(decisions)} decision{'s' if len(decisions) != 1 else ''} recorded" if decisions else ""
    return 0, f"session note attached to {head[:12]} (refs/notes/{ref}){extra}"


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
