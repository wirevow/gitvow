"""Outcomes: what happened to the change a decision permitted.

Every decision in the record is a label with no ground truth: nobody knows whether allowing something was right.
This module grades each decision-bearing commit by what the world did with it afterwards, and writes the grade
beside the decision, appended on its own notes ref (`refs/notes/gitvow/outcomes`), never rewriting the original.

Two sources, both read-only, and the note names which one spoke:

- **The pull-request stream**, through the forge's API (GitHub in this version, via `gh`): the pull request that
  contained the commit, and whether it was merged or closed unmerged.
- **Git history**: whether the commit is reachable from a production branch (landed without a pull request), and
  whether a later commit reverts it (`This reverts commit <sha>`), and how long after it landed.

The grade, per decision on the commit:

| answer   | landed (merged or direct), not reverted in the window | landed, reverted in the window, or closed | open / unknown |
|----------|-------------------------------------------------------|-------------------------------------------|----------------|
| accepted | held                                                  | not_held                                  | pending        |
| declined | overridden                                            | held                                      | pending        |
| referred | pending                                               | pending                                   | pending        |

The revert window is policy (`outcomes.revert_window_days`, default 1): the public census found that agent work
that gets reverted is reverted within about a day. A revert after the window is recorded, and does not change the
grade. Nothing here is per person; the summary counts classes of finding and outcomes, and the note carries the
answer and the finding, which are already on the commit.
"""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404 - argv only, no shell
import time
from typing import Any, Callable

from . import decisions as dec
from .export import remote_name
from .state import git, toplevel

OUTCOMES_REF = "gitvow/outcomes"  # refs/notes/gitvow/outcomes; pushed by the pre-push hook like every gitvow ref
SCHEMA = 1
DEFAULT_WINDOW_DAYS = 1
DEFAULT_PRODUCTION = ("main", "master", "production")
VERDICTS = ("merged", "closed", "open", "direct", "unknown")
OUTCOMES = ("held", "not_held", "overridden", "pending")
_REVERT_RE = re.compile(r"This reverts commit ([0-9a-f]{7,40})", re.I)
_GH_TIMEOUT = 20


class ScmUnavailableError(Exception):
    """The forge could not be asked; git alone will speak."""


def settings(pol: dict[str, Any] | None) -> dict[str, Any]:
    cfg = (pol or {}).get("outcomes") or {}
    days = cfg.get("revert_window_days", DEFAULT_WINDOW_DAYS)
    branches = tuple(((pol or {}).get("decisions") or {}).get("production_branches") or DEFAULT_PRODUCTION)
    return {"revert_window_days": int(days), "production_branches": branches}


# --- git ------------------------------------------------------------------------------------------------------


def decision_commits(cwd: str, since: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
    """Commits carrying an accepted, declined or referred trailer, newest first: sha, ts, date, decisions."""
    args = ["log", f"-{limit}", "--format=%H%x00%ct%x00%ad%x00%B%x01", "--date=short"]
    if since:
        args.append(f"--since={since}")
    rc, out, _ = git(args, cwd)
    rows: list[dict[str, Any]] = []
    if rc != 0:
        return rows
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip() or "Gitvow-" not in rec:
            continue
        sha, ts, date, body = rec.split("\x00", 3)
        ds = [t for t in dec.parse_trailers(body) if t["answer"] in ("accepted", "declined", "referred")]
        if not ds:
            continue
        rows.append({"sha": sha, "ts": int(ts) if ts.isdigit() else 0, "date": date, "decisions": ds})
    return rows


def reverts(cwd: str, limit: int = 5000) -> dict[str, dict[str, Any]]:
    """Reverted sha prefix -> the revert commit (sha, ts). Read from `This reverts commit <sha>` in messages."""
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ct%x00%B%x01"], cwd)
    found: dict[str, dict[str, Any]] = {}
    if rc != 0:
        return found
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if "reverts commit" not in rec.lower():
            continue
        sha, ts, body = rec.split("\x00", 2)
        for m in _REVERT_RE.finditer(body):
            found.setdefault(m.group(1), {"sha": sha, "ts": int(ts) if ts.isdigit() else 0})
    return found


def revert_of(sha: str, table: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for prefix, r in table.items():
        if sha.startswith(prefix) or prefix.startswith(sha):
            return r
    return None


def landed(cwd: str, sha: str, branches: tuple[str, ...]) -> bool:
    """Reachable from a production branch, remote-tracking or local."""
    for b in branches:
        for ref in (f"refs/remotes/origin/{b}", f"refs/heads/{b}"):
            rc, _, _ = git(["merge-base", "--is-ancestor", sha, ref], cwd)
            if rc == 0:
                return True
    return False


# --- the forge --------------------------------------------------------------------------------------------------


def _gh(args: list[str]) -> Any:
    """Run `gh api` and return parsed JSON. Read-only: only GET endpoints are ever passed."""
    try:
        r = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=_GH_TIMEOUT)  # nosec B603 B607
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ScmUnavailableError(str(e)[:120]) from e
    if r.returncode != 0:
        raise ScmUnavailableError((r.stderr or r.stdout).strip()[:160] or f"gh exited {r.returncode}")
    try:
        return json.loads(r.stdout or "null")
    except ValueError as e:
        raise ScmUnavailableError("gh returned no JSON") from e


def github_repo(cwd: str) -> tuple[str, str] | None:
    """(owner, repo) when origin is on github.com, else None."""
    name = remote_name(cwd)
    parts = name.split("/")
    if len(parts) >= 3 and parts[0].lower() == "github.com":
        return parts[1], parts[2]
    return None


def pulls_for(cwd: str, sha: str, gh: Callable[[list[str]], Any] = _gh) -> dict[str, Any] | None:
    """The pull request that carried the commit: merged one first, else the newest. None when there was none."""
    repo = github_repo(cwd)
    if repo is None:
        raise ScmUnavailableError("origin is not on github.com")
    owner, name = repo
    rows = gh([f"repos/{owner}/{name}/commits/{sha}/pulls"])
    if not isinstance(rows, list) or not rows:
        return None
    merged = [p for p in rows if p.get("merged_at")]
    p = merged[0] if merged else sorted(rows, key=lambda x: str(x.get("created_at") or ""), reverse=True)[0]
    return {
        "number": p.get("number"),
        "state": p.get("state"),
        "merged_at": p.get("merged_at"),
        "closed_at": p.get("closed_at"),
        "url": p.get("html_url"),
    }


# --- grading ----------------------------------------------------------------------------------------------------


def _iso_ts(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(time.mktime(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone)
    except ValueError:
        return None


def verdict_for(cwd: str, sha: str, pr: dict[str, Any] | None, branches: tuple[str, ...]) -> tuple[str, str]:
    """(verdict, source). A pull request speaks first; git says whether the commit landed without one."""
    if pr is not None:
        if pr.get("merged_at"):
            return "merged", "github"
        if pr.get("state") == "closed":
            return "closed", "github"
        return "open", "github"
    return ("direct", "git") if landed(cwd, sha, branches) else ("unknown", "git")


def grade(answer: str, verdict: str, reverted_in_window: bool) -> str:
    if answer == "referred" or verdict in ("open", "unknown"):
        return "pending"
    landed_ok = verdict in ("merged", "direct") and not reverted_in_window
    if answer == "accepted":
        return "held" if landed_ok else "not_held"
    if answer == "declined":
        return "overridden" if landed_ok else "held"
    return "pending"


def read(cwd: str, sha: str) -> dict[str, Any] | None:
    rc, out, _ = git(["notes", f"--ref={OUTCOMES_REF}", "show", sha], cwd)
    if rc != 0 or not out:
        return None
    head, _, body = out.partition("\n")
    if head.strip() != "gitvow-outcome":
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _final(note: dict[str, Any] | None, window_s: int, now: int) -> bool:
    """A grade nothing can change any more: a closed pull request, or a landing whose revert window has passed."""
    if not note:
        return False
    v = (note.get("verdict") or {}).get("kind")
    if v == "closed":
        return True
    if v in ("merged", "direct"):
        at = (note.get("verdict") or {}).get("landed_ts") or 0
        return bool(at) and now - int(at) > window_s
    return False


def run(
    cwd: str,
    pol: dict[str, Any] | None = None,
    since: str | None = "90d",
    dry_run: bool = False,
    scm: bool = True,
    regrade: bool = False,
    gh: Callable[[list[str]], Any] = _gh,
    now: int | None = None,
) -> dict[str, Any]:
    """Grade every decision-bearing commit in the window; write one note per commit unless dry_run."""
    top = toplevel(cwd) or cwd
    cfg = settings(pol)
    window_s = cfg["revert_window_days"] * 86400
    now = now or int(time.time())
    table = reverts(top)
    rows: list[dict[str, Any]] = []
    scm_state: str | None = None  # None: not asked; "ok"; "off" once the forge failed, with scm_reason
    scm_reason = ""
    counts: dict[str, int] = dict.fromkeys(OUTCOMES, 0)
    verdicts: dict[str, int] = dict.fromkeys(VERDICTS, 0)
    reused = written = 0
    for c in decision_commits(top, _since(since)):
        existing = read(top, c["sha"])
        if existing and not regrade and _final(existing, window_s, now):
            note = existing
            reused += 1
        else:
            pr: dict[str, Any] | None = None
            if scm and scm_state != "off":
                try:
                    pr = pulls_for(top, c["sha"], gh)
                    scm_state = "ok"
                except ScmUnavailableError as e:
                    scm_state = "off"
                    scm_reason = str(e)
            kind, source = verdict_for(top, c["sha"], pr, cfg["production_branches"])
            landed_ts = (
                _iso_ts(pr.get("merged_at")) if pr and pr.get("merged_at") else (c["ts"] if kind == "direct" else None)
            )
            rv = revert_of(c["sha"], table)
            gap = (rv["ts"] - (landed_ts or c["ts"])) if rv else None
            in_window = bool(rv) and gap is not None and gap <= window_s
            note = {
                "schema": SCHEMA,
                "graded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "window_days": cfg["revert_window_days"],
                "verdict": {
                    "kind": kind,
                    "source": source,
                    "pull_request": pr["number"] if pr else None,
                    "url": pr.get("url") if pr else None,
                    "landed_ts": landed_ts,
                    "landed_at": time.strftime("%Y-%m-%d", time.localtime(landed_ts)) if landed_ts else None,
                },
                "revert": ({"sha": rv["sha"][:12], "after_seconds": gap, "in_window": in_window} if rv else None),
                "decisions": [
                    {"finding": d["finding"], "answer": d["answer"], "outcome": grade(d["answer"], kind, in_window)}
                    for d in c["decisions"]
                ],
            }
            if not dry_run:
                body = "gitvow-outcome\n" + json.dumps(note, indent=1)
                git(["notes", f"--ref={OUTCOMES_REF}", "add", "-f", "-m", body, c["sha"]], top)
                written += 1
        verdicts[note["verdict"]["kind"]] += 1
        for d in note["decisions"]:
            counts[d["outcome"]] += 1
        rows.append({"sha": c["sha"][:7], "date": c["date"], **note})
    out = {
        "since": _since(since),
        "window_days": cfg["revert_window_days"],
        "commits": len(rows),
        "decisions": sum(counts.values()),
        "outcomes": counts,
        "verdicts": verdicts,
        "scm": scm_state or ("off" if not scm else "not needed"),
        "written": written,
        "reused": reused,
        "dry_run": dry_run,
        "rows": rows,
    }
    if scm_state == "off" and scm:
        out["scm_reason"] = scm_reason or "unavailable"
    return out


def _since(since: str | None) -> str | None:
    from .export import since_date

    return since_date(since) if since else None


def summary(cwd: str, since_day: str | None = None) -> dict[str, Any]:
    """Counts from the notes already written, for the digest. Reads only; never asks the forge."""
    top = toplevel(cwd) or cwd
    counts: dict[str, int] = dict.fromkeys(OUTCOMES, 0)
    verdicts: dict[str, int] = dict.fromkeys(VERDICTS, 0)
    graded = 0
    window = None
    for c in decision_commits(top, since_day):
        n = read(top, c["sha"])
        if not n:
            continue
        graded += 1
        window = n.get("window_days", window)
        verdicts[(n.get("verdict") or {}).get("kind") or "unknown"] += 1
        for d in n.get("decisions") or []:
            counts[d.get("outcome") or "pending"] += 1
    return {
        "commits_graded": graded,
        "decisions": sum(counts.values()),
        "outcomes": counts,
        "verdicts": verdicts,
        "window_days": window,
    }


# --- rendering ---------------------------------------------------------------------------------------------------

_LABEL = {"held": "held", "not_held": "did not hold", "overridden": "overridden", "pending": "pending"}


def summary_line(s: dict[str, Any]) -> str:
    """One digest line. Counts only; nothing about a person."""
    if not s.get("decisions"):
        return ""
    o = s["outcomes"]
    line = (
        f"Outcomes: {s['decisions']} decision{'s' if s['decisions'] != 1 else ''} graded on {s['commits_graded']} "
        f"commit{'s' if s['commits_graded'] != 1 else ''} · {o['held']} held · {o['not_held']} did not hold · "
        f"{o['overridden']} overridden · {o['pending']} pending"
    )
    if s.get("window_days") is not None:
        line += f" · revert window {s['window_days']}d"
    direct = (s.get("verdicts") or {}).get("direct") or 0
    if direct:
        line += f" · {direct} landed without a pull request"
    return line


def render(out: dict[str, Any]) -> str:
    lines = [
        summary_line(
            {
                "decisions": out["decisions"],
                "commits_graded": out["commits"],
                "outcomes": out["outcomes"],
                "verdicts": out["verdicts"],
                "window_days": out["window_days"],
            }
        )
        or "No decisions to grade in the window."
    ]
    if out.get("scm") == "off":
        lines.append(f"forge not asked ({out.get('scm_reason', 'unavailable')}); verdicts come from git alone")
    for r in out["rows"]:
        v = r["verdict"]
        where = f"PR #{v['pull_request']}" if v.get("pull_request") else v["kind"]
        when = f" {v['landed_at']}" if v.get("landed_at") else ""
        rv = r.get("revert")
        revert = (
            f"  reverted {rv['sha'][:7]} after {_dur(rv['after_seconds'])}{'' if rv['in_window'] else ' (after the window)'}"
            if rv
            else ""
        )
        lines.append(f"{r['sha']}  {r['date']}  {v['kind']:8} {where}{when}{revert}")
        for d in r["decisions"]:
            lines.append(f"    {_LABEL[d['outcome']]:13} {d['answer']:9} {d['finding']}")
    tail = []
    if out.get("dry_run"):
        tail.append("dry run: nothing written")
    else:
        tail.append(f"{out['written']} note{'s' if out['written'] != 1 else ''} written to refs/notes/{OUTCOMES_REF}")
        if out.get("reused"):
            tail.append(f"{out['reused']} final grade{'s' if out['reused'] != 1 else ''} kept")
    lines.append("; ".join(tail))
    return "\n".join(lines) + "\n"


def _dur(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"
