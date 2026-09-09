"""The record answers the next session: why, trace, recall, handoff. Read-only over trailers, notes, snapshots, ledger."""

from __future__ import annotations

import glob
import json
import os
import re
from typing import Any

from .hooks import LEGACY_NOTES_REF, notes_ref
from .report import STEP_RE, TRAILER_RE
from .snapshots import list_snapshots
from .state import git, load_state, toplevel

MAX_PLAN = 200


def _note(cwd: str, sha: str, sid: str) -> dict[str, Any] | None:
    for ref in (notes_ref(sid), LEGACY_NOTES_REF):
        rc, body, _ = git(["notes", f"--ref={ref}", "show", sha], cwd)
        if rc == 0 and body.startswith("gitvow-session"):
            try:
                return json.loads(body.split("\n", 1)[1])
            except (json.JSONDecodeError, IndexError):
                return None
    return None


def _commits(cwd: str, path: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    args = ["log", f"-{limit}", "--format=%H%x00%ad%x00%s%x00%B%x01", "--date=short"]
    if path:
        args += ["--follow", "--", path]
    rc, out, _ = git(args, cwd)
    rows: list[dict[str, Any]] = []
    if rc != 0:
        return rows
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, date, subject, body = rec.split("\x00", 3)
        m = TRAILER_RE.search(body)
        row: dict[str, Any] = {
            "sha": sha,
            "short": sha[:7],
            "date": date,
            "subject": subject,
            "kind": "person",
            "session": None,
            "step": None,
            "note": None,
        }
        if m:
            sm = STEP_RE.search(body)
            row.update(
                {
                    "kind": "agent",
                    "session": m.group(1),
                    "step": int(sm.group(1)) if sm else None,
                    "note": _note(cwd, sha, m.group(1)),
                }
            )
        rows.append(row)
    return rows


def _plan(note: dict[str, Any] | None) -> str:
    p = ((note or {}).get("last_stated_plan") or "").strip().replace("\n", " ")
    return (p[: MAX_PLAN - 1] + "…") if len(p) > MAX_PLAN else p


def why(cwd: str, path: str) -> str:
    top = toplevel(cwd) or cwd
    rel = os.path.relpath(os.path.abspath(path), top) if os.path.isabs(path) else path
    rows = _commits(top, rel)
    if not rows:
        return f"{rel}: no commits found"
    agent = [r for r in rows if r["kind"] == "agent"]
    sessions = {r["session"] for r in agent}
    lines = [
        f"{rel} · {len(agent)} agent commit{'s' if len(agent) != 1 else ''}, {len(rows) - len(agent)} human commit{'s' if len(rows) - len(agent) != 1 else ''}, {len(sessions)} session{'s' if len(sessions) != 1 else ''}",
        "",
    ]
    snaps = {s["ref"] for s in list_snapshots(top) if s.get("file") == rel}
    for r in rows[:30]:
        if r["kind"] == "person":
            lines.append(f"{r['date']}  {r['short']}  (person)  {r['subject'][:70]}")
            continue
        att = (r["note"] or {}).get("attribution") or {}
        share = att.get("agent_share")
        human = att.get("lines_changed_by_human_after_agent") or 0
        extra = f"agent share {share:.2f}" if isinstance(share, (int, float)) else "attribution n/a"
        if human:
            extra += f" · {human} line{'s' if human != 1 else ''} changed by a person afterwards"
        step = f"step {r['step']}" if r["step"] is not None else ""
        lines.append(f"{r['date']}  {r['short']}  session {r['session'][:8]} {step:<8} {extra}")
        plan = _plan(r["note"])
        lines.append(
            f"  {plan}"
            if plan
            else "  (no plan stated before committing)"
            if r["note"]
            else "  (session note not available; run gitvow push-notes on the author's machine)"
        )
    if snaps:
        lines += [
            "",
            f"{len(snaps)} snapshot{'s' if len(snaps) != 1 else ''} of this file from agent edits; see gitvow snapshots",
        ]
    return "\n".join(lines)


def trace(cwd: str, spec: str) -> str:
    top = toplevel(cwd) or cwd
    m = re.match(r"^(.*?)(?::(\d+)(?:-(\d+))?)?$", spec)
    path = m.group(1) if m else spec
    rel = os.path.relpath(os.path.abspath(path), top) if os.path.isabs(path) else path
    args = ["blame", "--line-porcelain"]
    if m and m.group(2):
        args += ["-L", f"{m.group(2)},{m.group(3) or m.group(2)}"]
    rc, out, err = git([*args, "--", rel], top)
    if rc != 0:
        return err or f"cannot blame {rel}"
    shas: list[str] = []
    line_no = int(m.group(2)) if m and m.group(2) else 1
    lines_by_sha: dict[str, list[int]] = {}
    for ln in out.splitlines():
        if re.match(r"^[0-9a-f]{40} ", ln):
            sha = ln.split()[0]
            parts = ln.split()
            final = int(parts[2]) if len(parts) > 2 else line_no
            lines_by_sha.setdefault(sha, []).append(final)
            if sha not in shas:
                shas.append(sha)
    info: dict[str, dict[str, Any]] = {}
    for sha in shas:
        rc, body, _ = git(["log", "-1", "--format=%B", sha], top)
        tm = TRAILER_RE.search(body or "")
        if tm:
            sm = STEP_RE.search(body)
            info[sha] = {
                "kind": "agent",
                "session": tm.group(1),
                "step": int(sm.group(1)) if sm else None,
                "note": _note(top, sha, tm.group(1)),
            }
        else:
            info[sha] = {"kind": "person"}
    # group consecutive lines per sha
    out_lines = [f"{rel}" + (f":{m.group(2)}-{m.group(3) or m.group(2)}" if m and m.group(2) else "")]
    for sha in shas:
        nums = sorted(lines_by_sha[sha])
        ranges, start, prev = [], nums[0], nums[0]
        for n in nums[1:]:
            if n != prev + 1:
                ranges.append((start, prev))
                start = n
            prev = n
        ranges.append((start, prev))
        rtxt = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in ranges[:6]) + (" …" if len(ranges) > 6 else "")
        i = info[sha]
        if i["kind"] == "agent":
            step = f"step {i['step']}" if i["step"] is not None else ""
            plan = _plan(i["note"])
            out_lines.append(
                f"lines {rtxt:<14} agent   {sha[:7]}  session {i['session'][:8]} {step}"
                + (f'  "{plan}"' if plan else "")
            )
        else:
            out_lines.append(f"lines {rtxt:<14} person  {sha[:7]}")
    return "\n".join(out_lines)


def recall(cwd: str, words: list[str], home: str | None = None, limit: int = 10) -> str:
    top = toplevel(cwd) or cwd
    terms = [w.lower() for w in words if w.strip()]
    if not terms:
        return "give one or more words to recall"
    hits: dict[str, dict[str, Any]] = {}
    for r in _commits(top, None, 2000):
        if r["kind"] != "agent":
            continue
        hay = " ".join(
            [
                r["subject"],
                _plan(r["note"]),
                " ".join((r["note"] or {}).get("files_written_by_agent_this_session") or []),
            ]
        ).lower()
        score = sum(1 for t in terms if t in hay)
        if not score:
            continue
        h = hits.setdefault(
            r["session"],
            {"session": r["session"], "date": r["date"], "commits": 0, "plan": "", "score": 0, "source": "notes"},
        )
        h["commits"] += 1
        h["score"] = max(h["score"], score)
        h["plan"] = h["plan"] or _plan(r["note"]) or r["subject"]
    led = os.path.join(home or os.path.expanduser("~"), ".gitvow", "ledger")
    for f in glob.glob(os.path.join(led, "*.json")):
        try:
            rec = json.load(open(f))  # noqa: SIM115
        except (OSError, json.JSONDecodeError):
            continue
        if os.path.realpath(rec.get("repo") or "") != os.path.realpath(top):
            continue
        hay = (
            " ".join([rec.get("last_stated_plan") or ""] + [t.get("arg", "") for t in rec.get("tool_calls", [])])
        ).lower()
        score = sum(1 for t in terms if t in hay)
        if score and rec.get("session_id") not in hits:
            hits[rec["session_id"]] = {
                "session": rec["session_id"],
                "date": (rec.get("started") or "")[:10],
                "commits": len(rec.get("commits_during_session") or []),
                "plan": _plan(rec),
                "score": score,
                "source": "ledger",
            }
    if not hits:
        return f"nothing recorded mentions: {' '.join(words)}"
    rows = sorted(hits.values(), key=lambda h: (-h["score"], h["date"]), reverse=False)[:limit]
    out = [f"{len(hits)} session{'s' if len(hits) != 1 else ''} mention {' '.join(words)}:"]
    for h in rows:
        out.append(
            f'session {h["session"][:8]}  {h["date"]}  {h["commits"]} commit{"s" if h["commits"] != 1 else ""}  "{h["plan"]}"'
            + ("  (ledger only)" if h["source"] == "ledger" else "")
        )
    return "\n".join(out)


def handoff(cwd: str, session_id: str | None = None, home: str | None = None) -> str:
    top = toplevel(cwd) or cwd
    st = load_state(top)
    sid = session_id or st.get("session_id")
    led_dir = os.path.join(home or os.path.expanduser("~"), ".gitvow", "ledger")
    rec: dict[str, Any] = {}
    if sid and os.path.exists(os.path.join(led_dir, f"{sid}.json")):
        try:
            rec = json.load(open(os.path.join(led_dir, f"{sid}.json")))  # noqa: SIM115
        except (OSError, json.JSONDecodeError):
            rec = {}
    if not sid:
        return "no session known in this repository; pass --session <id>"
    commits = [r for r in _commits(top, None, 500) if r["session"] == sid]
    files = sorted(
        {f for r in commits for f in ((r["note"] or {}).get("files_written_by_agent_this_session") or [])}
        | set(st.get("agent_blobs", {}).keys() if st.get("session_id") == sid else [])
    )
    _, dirty, _ = git(["diff", "--shortstat", "HEAD"], top)
    snaps = list_snapshots(top, sid)
    plan = _plan(rec) or next((_plan(r["note"]) for r in commits if _plan(r["note"])), "")
    confirms = []
    gd_log = os.path.join(top, ".git", "gitvow-hooks.log")
    if os.path.exists(gd_log):
        with open(gd_log) as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                if e.get("kind") == "confirm_required" and e.get("session_id") == sid:
                    confirms.append(f"{e.get('detail', '')[:60]} ({e.get('reason', '')[:60]})")
    out = [f"# Handoff · session {sid[:8]} · repo {os.path.basename(top)}", ""]
    out.append(f"Last stated plan: {plan or '(none recorded)'}")
    out.append(
        "Commits this session: "
        + (", ".join(f"{r['short']} {r['subject'][:50]}" for r in commits) if commits else "none")
    )
    out.append("Files the agent wrote: " + (", ".join(files[:20]) if files else "none recorded"))
    out.append(
        f"Uncommitted now: {dirty.strip() or 'clean'}" + (f" · last snapshot {snaps[-1]['ref']}" if snaps else "")
    )
    out.append(
        f"Open confirmations: {len(confirms)}" + ("".join(f"\n  - {c}" for c in confirms[-5:]) if confirms else "")
    )
    if rec.get("tool_calls"):
        out.append(f"Tool calls in session: {len(rec['tool_calls'])}")
    u = rec.get("usage") or next(
        ((r["note"] or {}).get("usage") for r in commits if (r["note"] or {}).get("usage")), None
    )
    if u and u.get("total_tokens"):
        cost = f" · ${u['estimated_cost_usd']:.2f} estimated" if u.get("estimated_cost_usd") is not None else ""
        out.append(f"Tokens so far: {u['total_tokens'] / 1e3:.0f}k{cost}")
    out += [
        "",
        "Next agent: read `gitvow why <file>` for any file above before changing it; snapshots hold the exact versions this session produced.",
    ]
    return "\n".join(out)
