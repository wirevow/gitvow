"""Earned rules: findings this repository has answered the same way, by people the policy names, often enough.

A rule is context for the agent, not permission. The gate still asks; the card arrives with the rule attached.
Rules carry their evidence and dates and decay when nobody has confirmed them within the window.

Reaching the threshold does not make a rule. It makes a **proposal**, which an authority accepts or rejects,
and that acceptance is recorded exactly as a decision is, with a trailer and a note. Two reasons, and the
second is the one that matters.

The practical reason: a threshold cannot tell the four kinds of decision apart. A universal best practice is
already in the models and the linters, so earning it adds nothing and dilutes the set. An organisation-wide
standard belongs at the organisation and should be pushed down into repositories, not inferred upwards from
one of them. A repository- or path-specific call genuinely belongs here. A situational one-off — "this
exception", "staging only" — belongs nowhere: three accepts of an exception is not a rule, it is three
exceptions, and promoting it writes a mistake into the repository's constitution. The danger was never too
few rules. It is too many bad ones: a set padded with platitudes, one-off exceptions and calls made by
people who were not the right ones to make them produces a brief the agent should ignore, or worse, obeys.
That is the disease that kills a hand-maintained wiki, arriving by another road.

The constitutional reason: this project's whole claim is that authority is human and the machine never
accepts its own consequence. Creating binding policy out of a statistical pattern, with nobody accepting
that it should be policy, is the machine authoring policy. Counting is gitvow's job. Deciding that a count
means something is not.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import Any

from .decisions import authority, history_all, identity
from .redact import redact
from .state import git, toplevel

DEFAULT_THRESHOLD = 3
DEFAULT_DECAY_DAYS = 90
START = "<!-- gitvow:rules -->"
END = "<!-- /gitvow:rules -->"
MAX_EVIDENCE = 8
HISTORY_COMMITS = 3000
# A rule decision is not a session, so it does not go on a session's note ref. It gets one ref of its own,
# pushed by the same pre-push hook (refs/notes/gitvow/*) and read by `gitvow show`.
RULES_NOTES_REF = "gitvow/rules"
RULES_NOTE_HEADER = "gitvow-rule-decision"
RULES_NOTE_SCHEMA = 1
# `answer=` says which run the verdict was about, because accepting "declined three times" is not accepting
# "accepted three times": a contradicting decision later flips the run and must not inherit the acceptance.
RULE_TRAILER_RE = re.compile(
    r"^Gitvow-Rule-(Accepted|Rejected):\s*(.+?)(?: by (\S+))?(?: answer=(accepted|declined))?(?:: (.*))?$", re.M
)
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


def rule_decisions(cwd: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Every accepted or rejected proposal in this branch's history, newest first.

    Kept in commit trailers for the same reason decisions are: the verdict then travels with the code it
    governs, through merge, rebase and every branch forked afterwards, and no local file can be edited to
    give a repository rules nobody accepted.
    """
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ad%x00%B%x01", "--date=short"], cwd)
    rows: list[dict[str, Any]] = []
    if rc != 0:
        return rows
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip() or "Gitvow-Rule-" not in rec:
            continue
        sha, date, body = rec.split("\x00", 2)
        for m in RULE_TRAILER_RE.finditer(body):
            rows.append(
                {
                    "verdict": m.group(1).lower(),
                    "finding": m.group(2).strip(),
                    "by": m.group(3),
                    "answer": m.group(4),
                    "note": (m.group(5) or "").strip() or None,
                    "sha": sha[:7],
                    "date": date,
                }
            )
    return rows


def _standing(verdicts: list[dict[str, Any]], finding: str, answer: str) -> dict[str, Any] | None:
    """The latest verdict on this finding and answer, or None if nobody has ruled on it.

    A verdict written without `answer=` (by hand, or by a gitvow that did not record it) applies to whichever
    run is current, since the only reading that loses nothing is the broad one.
    """
    for v in verdicts:  # newest first
        if v["finding"] == finding and v["answer"] in (None, answer):
            return v
    return None


def derive(cwd: str, pol: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    """Rules from the branch's decision history. Only decisions by authorities count; a contradiction resets."""
    top = toplevel(cwd) or cwd
    threshold, decay = settings(pol)
    today_d = _dt.date.fromisoformat(today) if today else _dt.date.today()
    rows = history_all(top, pol=pol)  # newest first, with authority from the note when available
    verdicts = rule_decisions(top)
    by_finding: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_finding.setdefault(r["finding"], []).append(r)
    rules: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    decayed: list[dict[str, Any]] = []
    for finding, hist in by_finding.items():
        auth = [h for h in hist if h.get("authority") in ("policy", "commit-access")]
        if not auth:
            continue
        # A scoped answer is an exception, not a precedent. "Accepted, staging only" is a condition attached
        # to one situation; three of them are three exceptions, and a rule derived from them would tell the
        # agent the unconditional thing was agreed, which nobody agreed to. So a scoped answer is set aside
        # before the run is computed: it neither counts towards the threshold nor breaks a run, because it
        # was never a statement about the general case in either direction. It is still reported, as the
        # exceptions this finding has collected, since a finding with many exceptions and no precedent is
        # worth a person's attention.
        exceptions = [h for h in auth if h.get("scope")]
        eligible = [h for h in auth if not h.get("scope")]
        if not eligible:
            continue
        answer = eligible[0]["answer"]
        run = []
        for h in eligible:  # newest first: count the unbroken run of the latest answer
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
            "scopes": [],  # a rule can never carry a scope; kept so consumers written against 0.12 still read
            "exceptions": len(exceptions),
            "exception_scopes": sorted({h["scope"] for h in exceptions if h.get("scope")}),
            "commits": [h["sha"] for h in run[:5]],
            "evidence": [
                {"date": h["date"], "by": h.get("by"), "answer": h["answer"], "sha": h["sha"]}
                for h in sorted(run, key=lambda h: h["date"])[:MAX_EVIDENCE]
            ],
            "expires": expires.isoformat(),
            "contradicted_by": len(eligible) - len(run),
        }
        if len(run) < threshold:
            candidates.append(entry)
        elif expires < today_d:
            decayed.append(entry)
        else:
            _classify(entry, _standing(verdicts, finding, answer), run, threshold, rules, proposals, rejected)
    key = lambda e: (e["last"], e["count"])  # noqa: E731
    return {
        "repo": top,
        "threshold": threshold,
        "decay_days": decay,
        "rules": sorted(rules, key=key, reverse=True),
        "proposals": sorted(proposals, key=key, reverse=True),
        "rejected": sorted(rejected, key=key, reverse=True),
        "candidates": sorted(candidates, key=key, reverse=True),
        "decayed": sorted(decayed, key=key, reverse=True),
    }


def _classify(
    entry: dict[str, Any],
    v: dict[str, Any] | None,
    run: list[dict[str, Any]],
    threshold: int,
    rules: list[dict[str, Any]],
    proposals: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> None:
    """Sort a run that has met the threshold into in force, awaiting an authority, or rejected."""
    if v is None:
        proposals.append(entry)
        return
    if v["verdict"] == "accepted":
        entry.update({"accepted_by": v["by"], "accepted_on": v["date"], "accepted_in": v["sha"]})
        rules.append(entry)
        return
    # A rejection is not a veto for one commit and it is not permanent either. Re-proposing the same pattern
    # on the very next decision would make the rejection meaningless and would nag an authority who has
    # already answered; never re-proposing would make one "no" outlive the reason for it. So a rejection sets
    # a floor: the proposal returns only when a full threshold of decisions dated after the rejection has
    # accumulated, which is fresh evidence rather than a re-count of the evidence already refused.
    fresh = [h for h in run if h["date"] > v["date"]]
    entry.update(
        {
            "rejected_by": v["by"],
            "rejected_on": v["date"],
            "rejected_in": v["sha"],
            "rejected_reason": v["note"],
            "fresh": len(fresh),
        }
    )
    (proposals if len(fresh) >= threshold else rejected).append(entry)


def _kind(finding: str) -> str:
    if finding.startswith("remove route "):
        return "route-removal"
    if finding.startswith("route "):
        return "route"
    if finding.startswith("run "):
        return "command"
    return "edit"


def rule_for(cwd: str, pol: dict[str, Any], finding: str) -> dict[str, Any] | None:
    """The rule in force for this finding: accepted by an authority, not decayed. Never a proposal."""
    for r in derive(cwd, pol)["rules"]:
        if r["finding"] == finding:
            return r
    return None


def proposal_for(cwd: str, pol: dict[str, Any], finding: str) -> dict[str, Any] | None:
    """The proposal standing for this finding, if one is waiting on an authority."""
    for r in derive(cwd, pol)["proposals"]:
        if r["finding"] == finding:
            return r
    return None


def _sentence(r: dict[str, Any]) -> str:
    who = ", ".join(r["by"][:3]) or "authorities"
    times = f"{r['count']} time{'s' if r['count'] != 1 else ''}"
    accepted = f", accepted as a rule by {r['accepted_by']} on {r['accepted_on']}" if r.get("accepted_by") else ""
    if r["answer"] == "accepted":
        return (
            f"`{r['finding']}` has been accepted {times} ({r['first']} to {r['last']}, by {who}){accepted}. "
            "Expect the card to propose accept; still put it to the person."
        )
    return (
        f"`{r['finding']}` has been declined {times} ({r['first']} to {r['last']}, by {who}){accepted}. "
        "Propose the alternative first; if the change still needs it, say why on the card."
    )


def _proposal_sentence(r: dict[str, Any]) -> str:
    who = ", ".join(r["by"][:3]) or "authorities"
    exc = (
        f" {r['exceptions']} further answer{'s' if r['exceptions'] != 1 else ''} carried a scope and was set "
        "aside, a scoped answer being an exception rather than a precedent."
        if r.get("exceptions")
        else ""
    )
    again = (
        f" An earlier proposal was rejected on {r['rejected_on']}; this one rests on {r['fresh']} answers since."
        if r.get("rejected_on")
        else ""
    )
    return (
        f"`{r['finding']}` was {r['answer']} {r['count']} times ({r['first']} to {r['last']}, by {who}). "
        f"That is a pattern in the past, not agreement: nobody has accepted it as a rule.{exc}{again}"
    )


def render(d: dict[str, Any], for_agent: bool = True) -> str:
    """Markdown block for an instruction file or SessionStart context."""
    if not d["rules"] and not d.get("proposals"):
        return ""
    lines = [START]
    if d["rules"]:
        lines += [
            f"## What this repository has decided ({len(d['rules'])} earned rule{'s' if len(d['rules']) != 1 else ''})",
            "",
            "Derived by gitvow from decisions on earlier commits and accepted as rules by a named person. "
            "Context, not permission: the gate still asks at commit.",
            "",
        ]
        for r in d["rules"]:
            lines.append(f"- {_sentence(r)} Decays {r['expires']} unless confirmed again.")
    if d.get("proposals"):
        n = len(d["proposals"])
        lines += [
            *([""] if d["rules"] else []),  # no stray blank line when proposals are the only section
            f"## Proposed, not rules ({n})",
            "",
            "gitvow has counted a repeated answer here and is proposing it as a rule. **Nobody has accepted "
            "it.** A proposal is an observation about the past. It is not agreement, it grants nothing, it "
            "does not tell you what is allowed, and it changes nothing about what you must put to a person. "
            "Do not cite it to anyone as the repository's position. It becomes a rule only when a person the "
            "policy names runs `gitvow rules accept`.",
            "",
        ]
        for i, r in enumerate(d["proposals"], 1):
            # Numbered only for a person at a terminal, who passes the number to `gitvow rules accept`.
            # A number in an instruction file is noise the agent cannot act on.
            lines.append(f"{i}. {_proposal_sentence(r)}" if not for_agent else f"- {_proposal_sentence(r)}")
    if not for_agent:
        lines += aside(d)
    lines.append(END)
    return "\n".join(lines) + "\n"


def aside(d: dict[str, Any]) -> list[str]:
    """Everything that is not in force and not proposed, for a person at a terminal but never for the agent.

    The agent is given what the repository has agreed and, labelled, what it is being asked to agree. Runs
    short of the threshold, rejections and lapsed rules are governance bookkeeping: useful to whoever curates
    the set, noise in a brief that is meant to be short enough to be read.
    """
    lines: list[str] = []
    if d.get("rejected"):
        lines += ["", f"Rejected, awaiting fresh evidence (a full {d['threshold']} answers after the rejection):"]
        for r in d["rejected"]:
            lines.append(
                f"- {r['finding']}: {r['answer']} {r['count']} times; rejected by {r['rejected_by'] or 'unknown'} "
                f"on {r['rejected_on']}"
                + (f" ({r['rejected_reason']})" if r.get("rejected_reason") else "")
                + f"; {r['fresh']} answer{'s' if r['fresh'] != 1 else ''} since"
            )
    if d["candidates"]:
        lines += ["", f"Not yet proposed (fewer than {d['threshold']} consistent answers by authorities):"]
        for r in d["candidates"]:
            lines.append(
                f"- {r['finding']}: {r['answer']} {r['count']} times (last {r['last']} by {', '.join(r['by'])})"
                + (f"; {r['exceptions']} scoped exception(s) set aside" if r.get("exceptions") else "")
            )
    if d["decayed"]:
        lines += ["", "Decayed (no confirmation within the window):"]
        for r in d["decayed"]:
            lines.append(
                f"- {r['finding']}: {r['answer']} {r['count']} times, last {r['last']}, expired {r['expires']}"
            )
    return lines


def select(d: dict[str, Any], which: str | None, take_all: bool) -> list[dict[str, Any]]:
    """The proposals a `gitvow rules accept|reject` invocation names: --all, a listing number, or the finding."""
    props = d["proposals"]
    if take_all:
        if not props:
            raise ValueError("no proposals standing; `gitvow rules` shows what is in force and what is waiting")
        return props
    if not which:
        raise ValueError("say which proposal: a number from `gitvow rules`, the finding text, or --all")
    if which.isdigit():
        n = int(which)
        if not 1 <= n <= len(props):
            raise ValueError(f"no proposal {n}; `gitvow rules` numbers the proposals standing")
        return [props[n - 1]]
    hit = [p for p in props if p["finding"] == which]
    if not hit:
        raise ValueError(f"no proposal for {which!r}; `gitvow rules` lists the proposals standing")
    return hit


def decide_proposals(
    cwd: str,
    pol: dict[str, Any],
    entries: list[dict[str, Any]],
    verdict: str,
    reason: str | None = None,
    by: str | None = None,
    rules: list[tuple[str, str]] | None = None,
) -> tuple[str, list[str]]:
    """Record an authority's verdict on proposed rules as an empty commit plus a note. Returns (sha, trailers).

    Accepting a proposal creates precedent, which is itself a decision, so it is written the way decisions are
    written: a trailer that travels with the history, and a note carrying the evidence the verdict was made
    against. Without the note the record would say a rule was accepted but not what was in front of the person
    who accepted it, which is the part a reviewer a year later actually needs.
    """
    if verdict not in ("accepted", "rejected"):
        raise ValueError("verdict must be accepted or rejected")
    top = toplevel(cwd) or cwd
    ident, email, name = identity(cwd, by)
    auth = authority(pol, ident, email, name)
    if auth == "none":
        # `gitvow decide` records an answer by an unnamed person and marks it authority: none, because the
        # answer is still a fact about what happened. Creating policy is different: an acceptance by someone
        # the policy does not name would be a rule nobody with standing agreed to, which is the exact failure
        # this whole mechanism exists to prevent. So it is refused rather than recorded and discounted.
        raise ValueError(
            f"{ident} is not named under decisions.authorities, so cannot accept or reject a rule for this "
            "repository; ask someone who is, or pass --by with their identity if you are recording their verdict"
        )
    # `git commit --allow-empty` commits the index, so staged work would be swept into a commit whose whole
    # purpose is to say a rule was accepted. Refuse plainly rather than mix code into the governance record.
    rc, staged, _ = git(["diff", "--cached", "--name-only"], top)
    if rc == 0 and staged:
        raise ValueError(
            "there are staged changes; commit or stash them first, so the rule decision is an empty commit "
            "carrying nothing but the verdict"
        )
    note = redact(reason, rules)[:200] if reason and rules is not None else (reason or None)
    lines = []
    for e in entries:
        line = f"Gitvow-Rule-{verdict.capitalize()}: {e['finding']} by {ident} answer={e['answer']}"
        if note:
            line += f": {note}"
        lines.append(line)
    word = "accept" if verdict == "accepted" else "reject"
    subject = (
        f"gitvow: {word} {len(entries)} proposed rules"
        if len(entries) > 1
        else f"gitvow: {word} proposed rule — {entries[0]['finding']}"
    )
    body = "\n".join(
        f"{e['finding']}: {e['answer']} {e['count']} times by authorities, {e['first']} to {e['last']}."
        for e in entries
    )
    msg = f"{subject[:120]}\n\n{body}\n\n{word.capitalize()}ed by {ident} ({auth}).\n\n" + "\n".join(lines) + "\n"
    rc, _, err = git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", msg], top)
    if rc != 0:
        raise ValueError(err or "commit failed")
    _, head, _ = git(["rev-parse", "HEAD"], top)
    payload = {
        "schema": RULES_NOTE_SCHEMA,
        "verdict": verdict,
        "by": ident,
        "authority": auth,
        "reason": note,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "threshold": entries[0]["threshold"],
        "rules": [
            {
                "finding": e["finding"],
                "kind": e["kind"],
                "answer": e["answer"],
                "count": e["count"],
                "first": e["first"],
                "last": e["last"],
                "by": e["by"],
                "evidence": e["evidence"],
                "exceptions": e["exceptions"],
                "exception_scopes": e["exception_scopes"],
                "expires": e["expires"],
            }
            for e in entries
        ],
    }
    git(
        [
            "notes",
            f"--ref={RULES_NOTES_REF}",
            "add",
            "-f",
            "-m",
            RULES_NOTE_HEADER + "\n" + json.dumps(payload, indent=1),
            head,
        ],
        top,
    )
    return head, lines


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
