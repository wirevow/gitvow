"""Decisions: at-commit findings kept in the session state, the card, a person's answers, trailers, and history.

A finding is raised by an at-commit rule while the agent works and is put to a person when the agent commits.
The answer travels as Gitvow-Accepted / Gitvow-Declined trailers and a `decisions` array in the session note.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .redact import redact
from .state import git, load_state, save_state

TRAILER_RE = re.compile(r"^Gitvow-(Accepted|Declined|Open):\s*(.+?)(?: by (\S+))?(?: scope=(\S+))?(?:: (.*))?$", re.M)
REVISITS_RE = re.compile(r"^Gitvow-Revisits:\s*([0-9a-f]{7,40})\b", re.M)
MAX_EVIDENCE = 8
HISTORY_COMMITS = 3000


def open_findings(cwd: str) -> list[dict[str, Any]]:
    """Findings collected in this repository's session state, numbered from 1 in the order they were raised."""
    st = load_state(cwd)
    out = []
    for i, f in enumerate(st.get("findings") or [], 1):
        out.append({"n": i, **f})
    return out


def undecided(cwd: str) -> list[dict[str, Any]]:
    return [f for f in open_findings(cwd) if not f.get("decision")]


def add(cwd: str, findings: list[dict[str, Any]], step: int, tool: str, rules: list[tuple[str, str]] | None) -> int:
    """Merge newly raised findings into the state; the same finding raised again only counts. Returns how many are new."""
    st = load_state(cwd)
    have: list[dict[str, Any]] = st.setdefault("findings", [])
    new = 0
    for f in findings:
        text = redact(f["finding"], rules) if rules is not None else f["finding"]
        for h in have:
            if h["finding"] == text:
                h["raised"] = h.get("raised", 1) + 1
                h["last_tool"] = tool
                break
        else:
            ev = [redact(e, rules)[:200] if rules is not None else e[:200] for e in (f.get("evidence") or [])]
            have.append(
                {
                    "finding": text,
                    "kind": f["kind"],
                    "subject": redact(f["subject"], rules) if rules is not None else f["subject"],
                    "path": f.get("path", ""),
                    "reason": redact(f["reason"], rules)[:300] if rules is not None else f["reason"][:300],
                    "evidence": ev[:MAX_EVIDENCE],
                    "raised": 1,
                    "step": step,
                    "raised_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "last_tool": tool,
                    "decision": None,
                }
            )
            new += 1
    save_state(cwd, st)
    return new


def identity(cwd: str, by: str | None = None) -> tuple[str, str, str]:
    """(trailer identity, email, name). The trailer uses the email's local part, else the name with spaces joined."""
    _, email, _ = git(["config", "user.email"], cwd)
    _, name, _ = git(["config", "user.name"], cwd)
    if by:
        return re.sub(r"\s+", "-", by.strip()), email, name
    if email and "@" in email:
        return email.split("@", 1)[0], email, name
    return re.sub(r"\s+", "-", name.strip()) or "unknown", email, name


def authority(pol: dict[str, Any], ident: str, email: str, name: str) -> str:
    """'policy' when named under decisions.authorities, 'commit-access' when no list is configured, else 'none'."""
    listed = [str(a).lower() for a in (pol.get("decisions") or {}).get("authorities") or []]
    if not listed:
        return "commit-access"
    auth = set(listed) | {a.split("@", 1)[0] for a in listed if "@" in a}  # an email also names its local part
    mine = {ident.lower(), email.lower(), (email.split("@", 1)[0] if email else "").lower(), name.lower()}
    mine.discard("")
    return "policy" if mine & auth else "none"


def parse_trailers(body: str) -> list[dict[str, Any]]:
    out = []
    for m in TRAILER_RE.finditer(body or ""):
        out.append(
            {
                "answer": {"Accepted": "accepted", "Declined": "declined", "Open": "open"}[m.group(1)],
                "finding": m.group(2).strip(),
                "by": m.group(3),
                "scope": m.group(4),
                "note": (m.group(5) or "").strip() or None,
            }
        )
    return out


SESSION_RE = re.compile(r"^Gitvow-Session:\s*(\S+)", re.M)


def _note_decisions(cwd: str, sha: str, body: str) -> dict[str, dict[str, Any]]:
    """finding -> note entry for a commit, when its session note is available locally."""
    import json

    m = SESSION_RE.search(body)
    if not m:
        return {}
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", m.group(1))
    rc, note, _ = git(["notes", f"--ref=gitvow/{sid}", "show", sha], cwd)
    if rc != 0 or not note.startswith("gitvow-session"):
        return {}
    try:
        data = json.loads(note.split("\n", 1)[1])
    except (json.JSONDecodeError, IndexError):
        return {}
    return {d.get("finding"): d for d in data.get("decisions") or [] if isinstance(d, dict)}


def history_all(
    cwd: str, limit: int = HISTORY_COMMITS, finding: str | None = None, pol: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Every decision (not open) in this branch's history, newest first, with authority from the note.

    Without a note, authority is 'commit-access' when the trailer names someone: the person had commit access.
    """
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ad%x00%B%x01", "--date=short"], cwd)
    rows: list[dict[str, Any]] = []
    if rc != 0:
        return rows
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip() or "Gitvow-" not in rec:
            continue
        sha, date, body = rec.split("\x00", 2)
        ts = [t for t in parse_trailers(body) if t["answer"] != "open" and (finding is None or t["finding"] == finding)]
        if not ts:
            continue
        notes = _note_decisions(cwd, sha, body)
        for t in ts:
            n = notes.get(t["finding"]) or {}
            if n.get("authority"):
                auth = n["authority"]
            elif not t.get("by"):
                auth = "none"
            else:
                auth = authority(pol or {}, t["by"], "", "")
            rows.append(
                {
                    **t,
                    "sha": sha[:7],
                    "date": date,
                    "authority": auth,
                    "kind": n.get("kind"),
                }
            )
    return rows


def history(cwd: str, finding: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Earlier decisions on the same finding in this branch's history, newest first."""
    return history_all(cwd, limit, finding)


def proposal(hist: list[dict[str, Any]]) -> tuple[str | None, str]:
    """(proposed answer or None, sentence about the record)."""
    if not hist:
        return None, "no earlier decision."
    acc = [h for h in hist if h["answer"] == "accepted"]
    dec = [h for h in hist if h["answer"] == "declined"]
    last = hist[0]
    parts = []
    if acc:
        parts.append(f"accepted {len(acc)} time{'s' if len(acc) != 1 else ''}")
    if dec:
        parts.append(f"declined {len(dec)} time{'s' if len(dec) != 1 else ''}")
    scope = f" (scope {last['scope']})" if last.get("scope") else ""
    sentence = f"{', '.join(parts)}, last {last['answer']} by {last['by'] or 'unknown'} on {last['date']}{scope}."
    if acc and not dec:
        return "accept", sentence + " Proposed: accept."
    if dec and not acc:
        return "decline", sentence + " Proposed: decline."
    return None, sentence + " Mixed record; no proposal."


def mark_proposals(cwd: str, pol: dict[str, Any] | None = None) -> int:
    """Store what the record proposes on each undecided finding, so the note can say whether the answer matched."""
    st = load_state(cwd)
    n = 0
    for f in st.get("findings") or []:
        if f.get("decision") or "proposed" in f:
            continue
        p, _ = proposal(history(cwd, f["finding"]))
        f["proposed"] = p
        n += 1 if p else 0
    save_state(cwd, st)
    return n


def card(
    cwd: str,
    findings: list[dict[str, Any]] | None = None,
    for_agent: bool = True,
    pol: dict[str, Any] | None = None,
) -> str:
    """The one surface a developer meets: every open finding with its evidence and what the record proposes."""
    fs = findings if findings is not None else open_findings(cwd)
    if not fs:
        return "No open findings.\n"
    pending = [f for f in fs if not f.get("decision")]
    lines = []
    if for_agent:
        lines += [
            f"DECISIONS REQUIRED before this commit: {len(pending)} finding{'s' if len(pending) != 1 else ''} from this session.",
            "Put this card to the user. Record each answer with",
            '  gitvow decide <n> accept|decline [--scope <env-or-branch>] [--reason "<phrase>"]',
            "then run the commit again.",
            "",
        ]
    for f in fs:
        d = f.get("decision")
        head = f"{f['n']}. {f['finding']}"
        if d:
            head += f"   [{d['answer']} by {d['by']}" + (f", scope {d['scope']}" if d.get("scope") else "") + "]"
        lines.append(head)
        why = f["reason"]
        if f.get("raised", 1) > 1:
            why += f" (raised {f['raised']} times)"
        lines.append(f"   why: {why}")
        for e in (f.get("evidence") or [])[:4]:
            if e and e not in f["reason"]:
                lines.append(f"   evidence: {e}")
        if not d:
            _, sentence = proposal(history(cwd, f["finding"]))
            lines.append(f"   record: {sentence}")
            if pol is not None:
                from .rules import rule_for

                r = rule_for(cwd, pol, f["finding"])
                if r:
                    lines.append(
                        f"   rule: {r['answer']} {r['count']} times by authorities since {r['first']}; decays {r['expires']}."
                    )
    return "\n".join(lines) + "\n"


def decide(
    cwd: str,
    which: str,
    answer: str,
    pol: dict[str, Any],
    scope: str | None = None,
    reason: str | None = None,
    by: str | None = None,
    rules: list[tuple[str, str]] | None = None,
    user_turns: int | None = None,
) -> list[dict[str, Any]]:
    """Record a person's answer for finding `which` (1-based) or 'all'. Returns the findings decided."""
    if answer not in ("accept", "decline"):
        raise ValueError("answer must be accept or decline")
    st = load_state(cwd)
    fs: list[dict[str, Any]] = st.get("findings") or []
    if not fs:
        raise ValueError("no open findings in this repository")
    if which == "all":
        idx = list(range(len(fs)))
    else:
        try:
            n = int(which)
        except ValueError as e:
            raise ValueError("finding must be a number or 'all'") from e
        if not 1 <= n <= len(fs):
            raise ValueError(f"no finding {n}; run gitvow decisions")
        idx = [n - 1]
    ident, email, name = identity(cwd, by)
    auth = authority(pol, ident, email, name)
    note = redact(reason, rules)[:200] if reason and rules is not None else (reason or None)
    done = []
    for i in idx:
        fs[i]["decision"] = {
            "answer": "accepted" if answer == "accept" else "declined",
            "by": ident,
            "authority": auth,
            "scope": (scope or "").strip() or None,
            "note": note,
            "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_turns": user_turns,
        }
        done.append({"n": i + 1, **fs[i]})
    save_state(cwd, st)
    return done


def trailer_line(f: dict[str, Any]) -> str:
    d = f.get("decision")
    if not d:
        return f"Gitvow-Open: {f['finding']}"
    key = "Gitvow-Accepted" if d["answer"] == "accepted" else "Gitvow-Declined"
    line = f"{key}: {f['finding']} by {d['by']}"
    if d.get("scope"):
        line += f" scope={d['scope']}"
    if d.get("note"):
        line += f": {d['note']}"
    return line


def note_entries(findings: list[dict[str, Any]], card_user_turns: int | None) -> list[dict[str, Any]]:
    out = []
    for f in findings:
        d = f.get("decision") or {}
        turns = None
        if d.get("user_turns") is not None and card_user_turns is not None:
            turns = max(int(d["user_turns"]) - int(card_user_turns), 0)
        out.append(
            {
                "finding": f["finding"],
                "kind": f["kind"],
                "subject": f["subject"],
                "path": f.get("path", ""),
                "reason": f["reason"],
                "evidence": f.get("evidence") or [],
                "raised": f.get("raised", 1),
                "answer": d.get("answer", "open"),
                "by": d.get("by"),
                "authority": d.get("authority", "none") if d else "none",
                "scope": d.get("scope"),
                "note": d.get("note"),
                "decided_at": d.get("decided_at"),
                "human_turns_after_card": turns,
                "proposed": f.get("proposed"),
            }
        )
    return out


def take_committed(cwd: str, head: str) -> list[dict[str, Any]] | None:
    """Findings the post-commit hook attached to `head`, removed from the state once read."""
    st = load_state(cwd)
    lc = st.get("last_commit")
    if not lc:
        return None
    if lc.get("sha") and head and not head.startswith(lc["sha"]) and not lc["sha"].startswith(head):
        return None
    st.pop("last_commit", None)
    save_state(cwd, st)
    return lc.get("findings") or []


def decisions_of(cwd: str, sha: str) -> list[dict[str, Any]]:
    """Decision trailers on one commit, numbered from 1."""
    rc, body, _ = git(["log", "-1", "--format=%B", sha], cwd)
    if rc != 0:
        raise ValueError(f"no such commit: {sha}")
    return [{"n": i, **t} for i, t in enumerate(parse_trailers(body), 1)]


def revisit(
    cwd: str,
    sha: str,
    answer: str,
    pol: dict[str, Any],
    which: str | None = None,
    scope: str | None = None,
    reason: str | None = None,
    by: str | None = None,
    rules: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Answer a decision already on the branch again.

    Writes an empty commit carrying the new answer and `Gitvow-Revisits: <sha>`; the earlier trailer stays
    where it was, so the record keeps both.
    """
    if answer not in ("accept", "decline"):
        raise ValueError("answer must be accept or decline")
    ds = decisions_of(cwd, sha)
    if not ds:
        raise ValueError(f"{sha[:7]} carries no decision trailers")
    if which is None:
        if len(ds) > 1:
            listing = "\n".join(f"  {d['n']}. {d['answer']}: {d['finding']}" for d in ds)
            raise ValueError("several decisions on this commit; pass --finding <n>:\n" + listing)
        target = ds[0]
    else:
        try:
            target = ds[int(which) - 1]
        except (ValueError, IndexError) as e:
            raise ValueError(f"no decision {which} on {sha[:7]}") from e
    ident, _email, _name = identity(cwd, by)
    note = redact(reason, rules)[:200] if reason and rules is not None else (reason or None)
    f = {
        "finding": target["finding"],
        "decision": {
            "answer": "accepted" if answer == "accept" else "declined",
            "by": ident,
            "scope": (scope or "").strip() or None,
            "note": note,
        },
    }
    _, full, _ = git(["rev-parse", sha], cwd)
    msg = (
        f"gitvow: revisit decision on {full[:7]}\n\n"
        f"Was: {target['answer']} ({target['finding']}). Now: {f['decision']['answer']} by {ident}.\n\n"
        f"Gitvow-Revisits: {full}\n{trailer_line(f)}\n"
    )
    rc, _, err = git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", msg], cwd)
    if rc != 0:
        raise ValueError(err or "commit failed")
    _, head, _ = git(["rev-parse", "HEAD"], cwd)
    return head, trailer_line(f) + f" (revisits {full[:7]})"


def open_debt(cwd: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Gitvow-Open findings on the branch that no later revisit has answered."""
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ad%x00%B%x01", "--date=short"], cwd)
    if rc != 0:
        return []
    revisited: set[tuple[str, str]] = set()
    opens: list[dict[str, Any]] = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip() or "Gitvow-" not in rec:
            continue
        sha, date, body = rec.split("\x00", 2)
        m = REVISITS_RE.search(body)
        if m:
            for t in parse_trailers(body):
                revisited.add((m.group(1)[:7], t["finding"]))
        for t in parse_trailers(body):
            if t["answer"] == "open":
                opens.append({"sha": sha[:7], "date": date, "finding": t["finding"]})
    return [o for o in opens if (o["sha"], o["finding"]) not in revisited]
