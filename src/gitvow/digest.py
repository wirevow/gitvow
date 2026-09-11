"""Period summary from the record: trailers, session notes and the hook log over a time window."""

from __future__ import annotations

import collections
import json
import os
import re
import time
from typing import Any

from . import decisions as dec
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
            {
                "session": r["session"],
                "date": r["date"],
                "commits": 0,
                "plan": "",
                "shares": [],
                "human_after": 0,
                "cost": None,
                "tokens": 0,
                "unpriced": False,
            },
        )
        s["commits"] += 1
        s["date"] = min(s["date"], r["date"])
        note = r["note"] or {}
        s["plan"] = s["plan"] or _plan(note)
        u = note.get("usage") or {}
        if u.get("total_tokens") and (s["cost"] is None or u["total_tokens"] >= s["tokens"]):
            s["tokens"] = u["total_tokens"]  # notes carry running totals; the latest note is the session's figure
            s["cost"] = u.get("estimated_cost_usd")
            s["unpriced"] = bool(u.get("unpriced_models"))
            s["_in"] = u.get("input_tokens", 0) + u.get("cache_read_tokens", 0) + u.get("cache_write_tokens", 0)
            s["_out"] = u.get("output_tokens", 0) + u.get("reasoning_tokens", 0)
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
    now: collections.Counter[str] = collections.Counter()  # this period's cards, findings, restores, sessions
    before: collections.Counter[str] = collections.Counter()  # the period of the same length before it
    span_days = max((time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) - _day_ts(since_day)) / 86400, 1)
    prev_day = time.strftime("%Y-%m-%d", time.localtime(_day_ts(since_day) - span_days * 86400))
    sess_now: set[str] = set()
    sess_before: set[str] = set()
    log = os.path.join(top, ".git", "gitvow-hooks.log")
    if os.path.exists(log):
        with open(log) as fh:
            for ln in fh:
                try:
                    e = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                day = (e.get("ts") or "")[:10]
                if day < prev_day:
                    continue
                bucket, sset = (now, sess_now) if day >= since_day else (before, sess_before)
                kind = e.get("kind")
                if kind == "session_start" and e.get("session_id"):
                    sset.add(e["session_id"])
                elif kind == "card":
                    bucket["cards"] += 1
                    bucket["pre_answered"] += int(e.get("proposed") or 0)
                elif kind == "finding":
                    bucket["findings"] += int(e.get("new") or 0)
                elif kind == "restore":
                    bucket["restores"] += 1
                if day < since_day:
                    continue
                if kind in ("blocked", "confirm_required"):
                    gate[kind] += 1
                    reasons[e.get("reason") or "?"] += 1
    decided = _decisions_in_period(top, since_day)
    debt = dec.open_debt(top)
    referred = dec.referrals(top)
    from .policy import PolicyError, load_policy
    from .rules import derive

    rule_proposals: int | None = None
    try:
        d_rules = derive(top, load_policy(top))
        rules_in_force: int | None = len(d_rules["rules"])
        rule_proposals = len(d_rules["proposals"])
    except PolicyError:
        rules_in_force = None  # the digest renders without a valid policy
    sess_list = sorted(sessions.values(), key=lambda s: s["date"], reverse=True)
    total_cost = 0.0
    in_tok = out_tok = 0
    unpriced = 0
    for s in sess_list:
        s["share"] = round(sum(s["shares"]) / len(s["shares"]), 2) if s["shares"] else None
        del s["shares"]
        if s["cost"] is not None:
            total_cost += s["cost"]
        in_tok += s.pop("_in", 0)
        out_tok += s.pop("_out", 0)
        unpriced += 1 if s["unpriced"] else 0
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
        "cost": {
            "estimated_usd": round(total_cost, 2),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "unpriced_sessions": unpriced,
        },
        "files": [{"path": p, "commits": n, "sessions": len(file_sessions[p])} for p, n in files.most_common(10)],
        "human": [{"short": r["short"], "date": r["date"], "subject": r["subject"]} for r in human[:15]],
        "decisions": {
            **decided,
            "debt": len(debt),
            "debt_items": debt[:10],
            # Referrals are their own number and their own list. Rolled into debt they were invisible, and a
            # queue of questions pointed at the wrong person reads as a team behind on its answers.
            "referrals": len(referred),
            "referral_items": referred[:10],
            "rules_in_force": rules_in_force,
            "rule_proposals": rule_proposals,
        },
        "questions": {
            "cards": now["cards"],
            "sessions": len(sess_now) or len(sessions),
            "per_session": round(now["cards"] / (len(sess_now) or len(sessions)), 2)
            if (sess_now or sessions)
            else None,
            "per_session_before": round(before["cards"] / len(sess_before), 2) if sess_before else None,
            "findings": now["findings"],
            "immediate": gate["confirm_required"],
        },
        "payback": {
            "snapshots_restored": now["restores"],
            "pre_answered": now["pre_answered"],
            "answers_matching_proposal": decided["matched_proposal"],
        },
    }


def _day_ts(day: str) -> float:
    try:
        return time.mktime(time.strptime(day, "%Y-%m-%d"))
    except ValueError:
        return time.time()


def _decisions_in_period(top: str, since_day: str) -> dict[str, int]:
    """Accepted, declined, open and revisited trailers on commits in the period; answers that matched the proposal."""
    from .state import git

    rc, out, _ = git(["log", "-5000", f"--since={since_day}", "--format=%H%x00%B%x01"], top)
    c: collections.Counter[str] = collections.Counter()
    if rc != 0:
        return {"accepted": 0, "declined": 0, "open": 0, "referred": 0, "revisited": 0, "matched_proposal": 0}
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip() or "Gitvow-" not in rec:
            continue
        sha, body = rec.split("\x00", 1)
        if dec.REVISITS_RE.search(body):
            c["revisited"] += 1
        ts = dec.parse_trailers(body)
        for t in ts:
            c[t["answer"]] += 1
        if any(t["answer"] != "open" for t in ts):
            for n in dec._note_decisions(top, sha, body).values():
                wanted = {"accept": "accepted", "decline": "declined"}.get(n.get("proposed") or "")
                if wanted and wanted == n.get("answer"):
                    c["matched_proposal"] += 1
    return {k: c[k] for k in ("accepted", "declined", "open", "referred", "revisited", "matched_proposal")}


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
    c = d.get("cost") or {}
    if c.get("input_tokens") or c.get("output_tokens"):
        unp = (
            f" · {c['unpriced_sessions']} session{'s' if c['unpriced_sessions'] != 1 else ''} unpriced"
            if c.get("unpriced_sessions")
            else ""
        )
        out.append(
            f"Cost: ${c['estimated_usd']:.2f} estimated · {c['input_tokens'] / 1e6:.1f}M input, {c['output_tokens'] / 1e6:.1f}M output tokens{unp}"
        )
    ds, q, pb = d.get("decisions") or {}, d.get("questions") or {}, d.get("payback") or {}
    if ds:
        line = f"Decisions: {ds['accepted']} accepted · {ds['declined']} declined · {ds['open']} open"
        if ds.get("referred"):
            line += f" · {ds['referred']} referred"
        if ds.get("debt"):
            line += f" · decision debt {ds['debt']} (open findings nobody has answered)"
        if ds.get("referrals"):
            line += f" · awaiting a different person {ds['referrals']}"
        if ds.get("revisited"):
            line += f" · {ds['revisited']} revisited"
        if ds.get("rules_in_force") is not None:
            line += f" · earned rules in force {ds['rules_in_force']}"
        if ds.get("rule_proposals"):
            line += f" · {ds['rule_proposals']} proposed rule{'s' if ds['rule_proposals'] != 1 else ''} awaiting an authority"
        out.append(line)
    if q:
        per = "n/a" if q.get("per_session") is None else f"{q['per_session']:.2f}"
        was = f", was {q['per_session_before']:.2f}" if q.get("per_session_before") is not None else ""
        out.append(
            f"Questions: {q['cards']} card{'s' if q['cards'] != 1 else ''} over {q['sessions']} session{'s' if q['sessions'] != 1 else ''} "
            f"({per} per session{was}) · {q['findings']} findings collected · {q['immediate']} immediate confirmation{'s' if q['immediate'] != 1 else ''}"
        )
    if pb and any(pb.values()):
        out.append(
            f"Payback: {pb['snapshots_restored']} snapshot{'s' if pb['snapshots_restored'] != 1 else ''} restored · "
            f"{pb['pre_answered']} question{'s' if pb['pre_answered'] != 1 else ''} pre-answered by the record · "
            f"{pb['answers_matching_proposal']} answer{'s' if pb['answers_matching_proposal'] != 1 else ''} matched the proposal"
        )
    if ds.get("debt_items"):
        out += ["", "### Decision debt"]
        for o in ds["debt_items"]:
            out.append(f"{o['sha']}  {o['date'][5:]}  {o['finding']}  · gitvow revisit {o['sha']} accept|decline")
    if ds.get("referral_items"):
        out += ["", "### Referred, waiting on someone else"]
        for o in ds["referral_items"]:
            whom = f"→ {o['to']}" if o.get("to") else "→ nobody named"
            out.append(
                f"{o['sha']}  {o['date'][5:]}  {o['finding']}  {whom}  · "
                f"gitvow revisit {o['sha']} accept|decline --by <them>"
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
            cost = f"  ${s['cost']:.2f}" if s.get("cost") is not None else ""
            out.append(
                f"{s['session'][:8]}  {s['date'][5:]}  {s['commits']} commit{'s' if s['commits'] != 1 else ' '}  share {sh}{cost}{plan}{after}"
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
