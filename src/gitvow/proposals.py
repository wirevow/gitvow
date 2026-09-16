"""Proposed policy rules: what a batch loop found the gate letting through, priced, waiting for a person.

A proposal is a rule for `.gitvow/policy.json` that nobody has accepted: a `path_confirm` or `bash_confirm` entry
in gitvow's own rule forms, with the evidence that produced it (how many events, in how many repositories, by how
many engineers, and what it would cost in questions per engineer per week until earned rules converge). It comes
from outside this package, as a JSON file, and sits in `.git/gitvow-policy-proposals.json` until a person accepts
or rejects it.

Accepting writes the rule into the repository's policy file and commits that change alone, with the evidence in
the message and a new trailer name, `Gitvow-Policy-Accepted`. The policy is code; the acceptance is a reviewed
diff. Rejecting is an empty commit carrying `Gitvow-Policy-Rejected`, so the loop that proposed it can see the
answer in history and stop proposing it. Neither trailer is matched by an older gitvow, which is the safe
direction. Authority follows the rules verdicts: someone the policy names, when it names anyone.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from typing import Any

from .decisions import authority, identity
from .policy import DEFAULT_POLICY_PATH, PolicyError, _validate, rule_pattern
from .redact import redact
from .state import git, git_dir, toplevel

QUEUE_FILE = "gitvow-policy-proposals.json"
POLICY_FILE = os.path.join(".gitvow", "policy.json")
HISTORY_COMMITS = 3000
TIERS = ("commit", "observe", "immediate")
POLICY_TRAILER_RE = re.compile(
    r"^Gitvow-Policy-(Accepted|Rejected):\s*(\S+)(?: by (\S+))?(?: when=(\S+))?(?:: (.*))?$", re.M
)


def _queue_path(cwd: str) -> str | None:
    gd = git_dir(cwd)
    return os.path.join(gd, QUEUE_FILE) if gd else None


def load_queue(cwd: str) -> list[dict[str, Any]]:
    p = _queue_path(cwd)
    if not p or not os.path.exists(p):
        return []
    try:
        with open(p) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_queue(cwd: str, rows: list[dict[str, Any]]) -> None:
    p = _queue_path(cwd)
    if not p:
        raise ValueError("not inside a git repository")
    with open(p, "w") as fh:
        json.dump(rows, fh, indent=1)


def _candidates_from(data: Any) -> list[dict[str, Any]]:
    """Accept the loop's `--json` output (card + observe lists), a bare list of candidates, or a policy fragment."""
    if isinstance(data, dict) and ("card" in data or "observe" in data):
        return list(data.get("card") or []) + list(data.get("observe") or [])
    if isinstance(data, dict) and ("path_confirm" in data or "bash_confirm" in data):
        out = []
        for key in ("path_confirm", "bash_confirm"):
            for r in data.get(key) or []:
                out.append(
                    {
                        "kind": "edit" if key == "path_confirm" else "run",
                        "id": r.get("id"),
                        "reason": r.get("reason"),
                        "rule": r,
                    }
                )
        return out
    if isinstance(data, list):
        return data
    raise ValueError(
        "unrecognised proposal file: expected the loop's JSON output, a list of candidates, or a policy fragment"
    )


def import_proposals(cwd: str, path: str, rules: list[tuple[str, str]] | None = None) -> dict[str, int]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    queue = load_queue(cwd)
    have = {q["id"] for q in queue}
    answered = {v["id"] for v in verdicts(cwd)}
    counts = {"queued": 0, "duplicate": 0, "answered": 0, "refused": 0}
    for c in _candidates_from(data):
        rule = dict(c.get("rule") or {})
        pid = str(c.get("id") or rule.get("id") or "").strip()
        if not pid or not rule or not rule.get("reason"):
            counts["refused"] += 1
            continue
        rule.setdefault("id", pid)
        if "pattern" not in rule and not ("program" in rule and "verbs" in rule):
            counts["refused"] += 1
            continue
        try:
            re.compile(rule_pattern(rule) if c.get("kind") != "edit" else rule.get("pattern", ""))
        except re.error:
            counts["refused"] += 1
            continue
        if rules is not None:
            rule["reason"] = redact(str(rule["reason"]), rules)[:200]
        if pid in answered:
            counts["answered"] += 1
            continue
        if pid in have:
            counts["duplicate"] += 1
            continue
        queue.append(
            {
                "id": pid,
                "kind": "edit"
                if c.get("kind") == "edit" or ("pattern" in rule and "program" not in rule and c.get("kind") != "run")
                else "run",
                "rule": rule,
                "tier": rule.get("when") or c.get("tier") or "commit",
                "evidence": {
                    k: c.get(k)
                    for k in (
                        "events",
                        "sessions",
                        "repos",
                        "engineers",
                        "asks_card",
                        "asks_per_engineer_week_card",
                        "events_per_engineer_week",
                        "shapes",
                        "reads_left_alone",
                        "weight",
                    )
                    if c.get(k) is not None
                },
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        have.add(pid)
        counts["queued"] += 1
    save_queue(cwd, queue)
    return counts


def queue(cwd: str) -> list[dict[str, Any]]:
    """Proposals waiting, the ones that would cost the most attention first."""
    rows = sorted(
        load_queue(cwd), key=lambda r: (-(r["evidence"].get("weight") or 0), -(r["evidence"].get("events") or 0))
    )
    return [{"n": i, **r} for i, r in enumerate(rows, 1)]


def _pick(cwd: str, which: str) -> dict[str, Any]:
    q = queue(cwd)
    if not q:
        raise ValueError("no proposed policy rules in the queue; `gitvow policy import <file.json>` first")
    if which.isdigit():
        n = int(which)
        if not 1 <= n <= len(q):
            raise ValueError(f"no proposal number {n}; the queue has {len(q)}")
        return q[n - 1]
    for r in q:
        if r["id"] == which:
            return r
    raise ValueError(f"no queued proposal {which!r}")


def _policy_file(top: str) -> tuple[str, dict[str, Any], bool]:
    """The repository's policy file, created from the shipped default when the repository has none."""
    p = os.path.join(top, POLICY_FILE)
    created = False
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy(DEFAULT_POLICY_PATH, p)
        created = True
    with open(p) as fh:
        return p, json.load(fh), created


def decide(
    cwd: str,
    which: str,
    verdict: str,
    pol: dict[str, Any],
    when: str | None = None,
    reason: str | None = None,
    by: str | None = None,
    rules: list[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """Accept a proposal into `.gitvow/policy.json` as a reviewed commit, or reject it on an empty commit."""
    if verdict not in ("accepted", "rejected"):
        raise ValueError("verdict must be accepted or rejected")
    top = toplevel(cwd) or cwd
    row = _pick(cwd, which)
    ident, email, name = identity(cwd, by)
    auth = authority(pol, ident, email, name)
    if auth == "none":
        raise ValueError(
            f"{ident} is not named under decisions.authorities, so cannot accept or reject a policy rule for this "
            "repository; ask someone who is, or pass --by with their identity if you are recording their verdict"
        )
    rc, staged, _ = git(["diff", "--cached", "--name-only"], top)
    if rc == 0 and staged:
        raise ValueError(
            "there are staged changes; commit or stash them first, so the policy decision is a commit carrying nothing else"
        )
    note = redact(reason, rules)[:200] if reason and rules is not None else (reason or None)
    tier = when or row["tier"]
    if tier not in TIERS:
        raise ValueError(f"when must be one of {TIERS}")
    e = row["evidence"]
    ev_line = ", ".join(
        f"{k} {e[k]}" for k in ("events", "repos", "engineers", "asks_per_engineer_week_card") if e.get(k) is not None
    )
    tag = "Accepted" if verdict == "accepted" else "Rejected"
    line = f"Gitvow-Policy-{tag}: {row['id']} by {ident}"
    if verdict == "accepted":
        line += f" when={tier}"
    if note:
        line += f": {note}"
    if verdict == "accepted":
        p, policy, created = _policy_file(top)
        rule = dict(row["rule"])
        rule["when"] = tier
        key = "path_confirm" if row["kind"] == "edit" else "bash_confirm"
        policy.setdefault(key, [])
        if any(r.get("id") == rule["id"] for r in policy[key]):
            raise ValueError(f"the policy already has a {key} rule with id {rule['id']!r}")
        policy[key].append(rule)
        try:
            _validate(policy, p)
        except PolicyError as err:
            raise ValueError(f"the accepted rule would make the policy invalid: {err}") from err
        with open(p, "w") as fh:
            json.dump(policy, fh, indent=2)
            fh.write("\n")
        git(["add", "--", os.path.relpath(p, top)], top)
        subject = f"gitvow: accept proposed policy rule {row['id']} ({tier})"
        body = (
            f"{rule['reason']}\n\nProposed from what the gate let through: {ev_line or 'no evidence recorded'}.\n"
            + (
                "The repository had no policy file; this commit creates it from the shipped default and adds the rule.\n"
                if created
                else ""
            )
            + f"\nAccepted by {ident} ({auth}).\n"
        )
    else:
        subject = f"gitvow: reject proposed policy rule {row['id']}"
        body = f"{row['rule'].get('reason', '')}\n\nProposed from what the gate let through: {ev_line or 'no evidence recorded'}.\n\nRejected by {ident} ({auth}).\n"
    msg = f"{subject}\n\n{body}\n{line}\n"
    args = ["-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", msg] + (
        ["--allow-empty"] if verdict == "rejected" else []
    )
    rc, _, err = git(args, top)
    if rc != 0:
        raise ValueError(err or "commit failed")
    _, head, _ = git(["rev-parse", "HEAD"], top)
    save_queue(cwd, [r for r in load_queue(cwd) if r["id"] != row["id"]])
    return head, line


def verdicts(cwd: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Every policy-proposal verdict on the branch, newest first."""
    top = toplevel(cwd) or cwd
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ad%x00%B%x01", "--date=short"], top)
    if rc != 0:
        return []
    rows = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if "Gitvow-Policy-" not in rec:
            continue
        sha, date, body = rec.split("\x00", 2)
        for m in POLICY_TRAILER_RE.finditer(body):
            rows.append(
                {
                    "verdict": m.group(1).lower(),
                    "id": m.group(2),
                    "by": m.group(3),
                    "when": m.group(4),
                    "note": (m.group(5) or "").strip() or None,
                    "sha": sha[:7],
                    "date": date,
                }
            )
    return rows


def render_queue(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No proposed policy rules waiting.\n"
    out = [f"{len(rows)} proposed policy rule{'s' if len(rows) != 1 else ''} waiting, highest consequence first:", ""]
    for r in rows:
        e = r["evidence"]
        rule = r["rule"]
        what = (
            f"program {rule['program']} verbs {', '.join(rule['verbs'])}"
            if rule.get("program")
            else f"pattern {rule.get('pattern')}"
        )
        price = (
            f"{e['asks_per_engineer_week_card']} asks/eng/wk until rules converge"
            if r["tier"] == "commit" and e.get("asks_per_engineer_week_card") is not None
            else f"{e.get('events_per_engineer_week', '?')} events/eng/wk recorded, 0 asks"
            if r["tier"] == "observe"
            else "asks on every call"
        )
        out.append(f"{r['n']}. [{r['tier']}] {r['id']}: {rule.get('reason')}")
        out.append(f"   {what}")
        out.append(
            f"   evidence: {e.get('events', '?')} events, {e.get('repos', '?')} repos, {e.get('engineers', '?')} engineers; {price}"
        )
        if e.get("shapes"):
            out.append(f"   seen at: {', '.join(str(s) for s in e['shapes'][:4])}")
    out += [
        "",
        "accept: gitvow policy accept <n> [--when commit|observe|immediate] [--reason R]   reject: gitvow policy reject <n> [--reason R]",
    ]
    return "\n".join(out) + "\n"
