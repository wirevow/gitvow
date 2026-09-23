"""Carry session notes onto a commit that replaced others: the squash merge.

`notes.rewriteRef` follows `--amend` and rebase because git tells the notes machinery which commit became which.
A squash, local or on a forge, creates a new commit with no such mapping, so the notes on the pull request's
commits stay behind on commits nothing references, and the trailers survive only if the squash message kept the
originals. `gitvow carry <base>..<head> <target>` reads every session note on the commits in the range and writes
one note per session onto the target: the newest note as the body, every source commit listed under
`carried_from` with its decisions and files, and the decisions of all of them merged, so `report`, `why` and
standing see the squash commit as what it is: the sum of the commits it replaced. The GitHub Action runs this
when a pull request is merged and pushes the refs.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .notes import find_note, ref_for, session_refs
from .state import git

SESSION_RE = re.compile(r"^Gitvow-Session:\s*(\S+)", re.M)


def _commits(cwd: str, rng: str) -> list[tuple[str, str]]:
    rc, out, err = git(["log", "--reverse", "--format=%H%x00%B%x01", rng], cwd)
    if rc != 0:
        raise ValueError(err or f"cannot list {rng}")
    rows = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if rec.strip():
            sha, body = rec.split("\x00", 1)
            rows.append((sha, body))
    return rows


def _notes_on(cwd: str, sha: str, body: str) -> dict[str, dict[str, Any]]:
    """ref -> note for every session ref holding a note on this commit (the trailer's first, then a scan)."""
    out: dict[str, dict[str, Any]] = {}
    m = SESSION_RE.search(body)
    if m:
        data, ref = find_note(cwd, sha, m.group(1))
        if data and ref:
            out[ref] = data
    for ref in session_refs(cwd):
        if ref in out:
            continue
        rc, text, _ = git(["notes", f"--ref={ref}", "show", sha], cwd)
        if rc == 0 and text.startswith("gitvow-session"):
            try:
                out[ref] = json.loads(text.split("\n", 1)[1])
            except (json.JSONDecodeError, IndexError):
                continue
    return out


def carry(cwd: str, rng: str, target: str) -> dict[str, Any]:
    """Write carried notes onto `target` for every session that noted a commit in `rng`. Idempotent."""
    rc, target_sha, err = git(["rev-parse", "--verify", f"{target}^{{commit}}"], cwd)
    if rc != 0:
        raise ValueError(err or f"unknown target {target}")
    per_ref: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    commits = _commits(cwd, rng)
    for sha, body in commits:
        for ref, note in _notes_on(cwd, sha, body).items():
            per_ref.setdefault(ref, []).append((sha, note))
    written = []
    for ref, sources in per_ref.items():
        newest = sources[-1][1]
        decisions: list[dict[str, Any]] = []
        files: list[str] = []
        carried_from = []
        for sha, note in sources:
            for d in note.get("decisions") or []:
                if isinstance(d, dict) and d not in decisions:
                    decisions.append(d)
            for f in note.get("files_in_commit") or []:
                if f not in files:
                    files.append(f)
            carried_from.append(
                {
                    "sha": sha,
                    "step": note.get("step"),
                    "decisions": note.get("decisions") or [],
                    "files_in_commit": (note.get("files_in_commit") or [])[:50],
                    "last_stated_plan": note.get("last_stated_plan"),
                }
            )
        merged = {
            **newest,
            "carried": True,
            "carried_from": carried_from,
            "decisions": decisions,
            "files_in_commit": files[:100],
            "step": None,
            "transcript": newest.get("transcript", "kept local; see ledger"),
        }
        body = "gitvow-session\n" + json.dumps(merged, indent=1)
        rc, _, err = git(["notes", f"--ref={ref}", "add", "-f", "-m", body, target_sha], cwd)
        if rc != 0:
            raise ValueError(err or f"cannot write note under {ref}")
        written.append({"ref": ref, "from": [s for s, _ in sources], "decisions": len(decisions)})
    return {"target": target_sha, "commits_in_range": len(commits), "notes_written": written}


def render(r: dict[str, Any]) -> str:
    if not r["notes_written"]:
        return (
            f"no session notes on the {r['commits_in_range']} commits in range; nothing carried to {r['target'][:12]}\n"
        )
    lines = [f"carried onto {r['target'][:12]} from {r['commits_in_range']} commits:"]
    for w in r["notes_written"]:
        lines.append(f"  refs/notes/{w['ref']}: {len(w['from'])} source commit(s), {w['decisions']} decision(s)")
    lines.append("push with: git push origin 'refs/notes/gitvow/*:refs/notes/gitvow/*'")
    return "\n".join(lines) + "\n"


def default_ref(cwd: str) -> str:
    return ref_for(None)
