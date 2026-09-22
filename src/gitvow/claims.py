"""Claims: what a person said about their system, confirmed by them, kept in git.

A claim is a statement in a person's own words ("we don't do any GET calls here, only List, CopyObject and
DeleteObject"), with a source and a binding to paths. It arrives as a *candidate*, extracted from sessions or
documents by a batch job outside this package, and sits in a queue in `.git/gitvow-claims.json` until a person
confirms or rejects it. A confirmation is recorded the way a rule verdict is: an empty commit carrying a new
trailer name, plus a note on `refs/notes/gitvow/claims` holding the verbatim text, the source pointer and the
edit if any. A confirmed claim informs the agent (rendered into the instruction files, handed over at session
start); it never answers the gate.

Two reaches. A claim that travels with the code (`travels_with: code`) is repo-reach and lives in this
repository's history. A claim that travels with the person (`preference`) is person-reach and stays in that
person's own `~/.gitvow/claims/`, read into their own sessions only. It is never committed and never exported.

The trailer grammar grows by new names only: `Gitvow-Claim-Confirmed` and `Gitvow-Claim-Rejected`. An older
gitvow does not match either, which is the safe direction.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .decisions import authority, identity
from .redact import redact
from .state import git, git_dir, toplevel

CLAIMS_NOTES_REF = "gitvow/claims"
CLAIMS_NOTE_HEADER = "gitvow-claim"
CLAIMS_NOTE_SCHEMA = 1
QUEUE_FILE = "gitvow-claims.json"
HISTORY_COMMITS = 3000
MAX_PATHS_IN_TRAILER = 8
START = "<!-- gitvow:claims -->"
END = "<!-- /gitvow:claims -->"

CLAIM_TRAILER_RE = re.compile(
    r"^Gitvow-Claim-(Confirmed|Rejected):\s*(clm_[0-9A-Z]{26})(?: by (\S+))?(?: paths=(\S+))?(?:: (.*))?$", re.M
)
CLAIM_ID_RE = re.compile(r"^clm_[0-9A-Z]{26}$")


# ---------------------------------------------------------------------------------------------------------------
# the queue: candidates nobody has answered yet
# ---------------------------------------------------------------------------------------------------------------


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


def _person_dir(home: str | None) -> str:
    return os.path.join(home or os.path.expanduser("~"), ".gitvow", "claims")


def import_candidates(
    cwd: str, path: str, home: str | None = None, rules: list[tuple[str, str]] | None = None
) -> dict[str, int]:
    """Read candidates (JSONL, one per line) into the queue; person-reach ones go to the person's own file.

    A candidate needs `claim_id`, `text` and `speaker`. Everything else is optional. Text is redacted at import,
    which is the write-time edge for this data class; a candidate whose text is entirely redacted is refused.
    """
    queue = load_queue(cwd)
    have = {q["claim_id"] for q in queue}
    already = set()
    for c in confirmed_and_rejected(cwd):
        already.add(c["claim_id"])
    counts = {"queued": 0, "person": 0, "duplicate": 0, "answered": 0, "refused": 0}
    person_rows: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                c = json.loads(line)
            except ValueError:
                counts["refused"] += 1
                continue
            cid, text, speaker = c.get("claim_id"), str(c.get("text") or "").strip(), str(c.get("speaker") or "")
            if not (isinstance(cid, str) and CLAIM_ID_RE.match(cid)) or not text or not speaker:
                counts["refused"] += 1
                continue
            if rules is not None:
                text = redact(text, rules)
                if not text.strip() or (
                    text.strip().startswith("[") and text.strip().endswith("]") and " " not in text
                ):
                    counts["refused"] += 1
                    continue
            if cid in already:
                counts["answered"] += 1
                continue
            row = {
                "claim_id": cid,
                "text": text,
                "speaker": speaker,
                "kind": c.get("kind") or "system",
                "travels_with": c.get("travels_with") or ("person" if c.get("kind") == "preference" else "code"),
                "paths": [str(p) for p in (c.get("paths") or [])],
                "confidence": float(c.get("confidence") or 0),
                "why": str(c.get("why") or "")[:300],
                "source": c.get("source") or {},
                "when": c.get("when"),
                "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            if row["travels_with"] == "person":
                person_rows.append(json.dumps(row, ensure_ascii=False))
                counts["person"] += 1
                continue
            if cid in have:
                counts["duplicate"] += 1
                continue
            queue.append(row)
            have.add(cid)
            counts["queued"] += 1
    save_queue(cwd, queue)
    if person_rows:
        d = _person_dir(home)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "preferences.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("\n".join(person_rows) + "\n")
    return counts


def queue(cwd: str, suggest: bool = True) -> list[dict[str, Any]]:
    """Unanswered repo-reach candidates, most confident first, numbered from 1, with suggested paths for those
    that arrived without a binding."""
    rows = sorted(load_queue(cwd), key=lambda r: -float(r.get("confidence") or 0))
    tree = repo_tree(cwd) if suggest else []
    out = []
    for i, r in enumerate(rows, 1):
        row = {"n": i, **r}
        if tree and not r.get("paths"):
            row["suggested"] = suggest_paths(r["text"], tree)
        out.append(row)
    return out


_STOP = frozenset(
    [
        "the",
        "this",
        "that",
        "with",
        "from",
        "into",
        "only",
        "when",
        "where",
        "which",
        "while",
        "about",
        "after",
        "before",
        "also",
        "just",
        "have",
        "has",
        "had",
        "will",
        "would",
        "should",
        "could",
        "been",
        "being",
        "were",
        "was",
        "are",
        "not",
        "and",
        "for",
        "but",
        "its",
        "it's",
        "they",
        "them",
        "then",
        "than",
        "these",
        "those",
        "there",
        "here",
        "what",
        "your",
        "our",
        "you",
        "all",
        "any",
        "each",
        "every",
        "some",
        "such",
        "very",
        "more",
        "most",
        "other",
        "same",
        "own",
        "over",
        "under",
        "again",
        "once",
        "because",
        "since",
        "although",
        "though",
        "whether",
        "either",
        "neither",
        "both",
    ]
)


def repo_tree(cwd: str) -> list[str]:
    top = toplevel(cwd)
    if not top:
        return []
    rc, out, _ = git(["ls-files"], top)
    return out.splitlines() if rc == 0 else []


def _terms(text: str) -> set[str]:
    words = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_./-]{1,}", text):
        w = w.strip("./-")
        for part in re.split(r"[_./-]", w):
            if len(part) >= 3 and part.lower() not in _STOP and not part.isdigit():
                words.add(part.lower())
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", w):  # camelCase pieces
            if len(part) >= 4 and part.lower() not in _STOP:
                words.add(part.lower())
        if len(w) >= 4 and w.lower() not in _STOP:
            words.add(w.lower())
    return words


def suggest_paths(text: str, tree: list[str], limit: int = 3) -> list[str]:
    """Directories (and single files) whose names share terms with the claim, best first. No model; equality on
    lower-cased path pieces against the claim's words, so a suggestion is checkable by reading it."""
    terms = _terms(text)
    if not terms or not tree:
        return []
    scores: dict[str, float] = {}
    for path in tree:
        pieces = [p.lower() for p in re.split(r"[/._-]", path) if p]
        hits = {t for t in terms if any(t == p or (len(t) >= 5 and len(p) >= 4 and (t in p or p in t)) for p in pieces)}
        if not hits:
            continue
        parts = path.split("/")
        # credit the file and every ancestor directory; directories win ties because they generalise
        for depth in range(1, min(len(parts), 3) + 1):
            key = "/".join(parts[:depth]) + ("/" if depth < len(parts) else "")
            scores[key] = scores.get(key, 0) + len(hits) * (1.0 if key.endswith("/") else 0.6)
    # deeper directories win ties: `etl/currency/` says more than `etl/`; one exact term is enough to suggest
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -kv[0].count("/"), kv[0]))
    best = ranked[0][1] if ranked else 0
    kept = [k for k, v in ranked if v >= max(1.0, best * 0.6)]
    # a parent adds nothing when a child scored at least as well: suggest `etl/currency/`, not also `etl/`
    kept = [k for k in kept if not any(c != k and c.startswith(k) and scores[c] >= scores[k] for c in kept)]
    # and a file adds nothing when its directory is suggested
    kept = [k for k in kept if k.endswith("/") or not any(d.endswith("/") and k.startswith(d) for d in kept)]
    return kept[:limit]


def _pick(cwd: str, which: str) -> dict[str, Any]:
    q = queue(cwd)
    if not q:
        raise ValueError("no candidate claims in the queue; `gitvow claims import <file.jsonl>` first")
    if which.isdigit():
        n = int(which)
        if not 1 <= n <= len(q):
            raise ValueError(f"no claim number {n}; the queue has {len(q)}")
        return q[n - 1]
    for r in q:
        if r["claim_id"] == which:
            return r
    raise ValueError(f"no queued claim {which!r}")


# ---------------------------------------------------------------------------------------------------------------
# the verdict: an empty commit with a new trailer name, plus a note
# ---------------------------------------------------------------------------------------------------------------


def _authority_for(pol: dict[str, Any], cwd: str, by: str | None, speaker: str) -> tuple[str, str]:
    ident, email, name = identity(cwd, by)
    if ident.lower() == speaker.lower() or (email and email.split("@")[0].lower() == speaker.lower()):
        return ident, "speaker"
    return ident, authority(pol, ident, email, name)


def decide(
    cwd: str,
    which: str,
    verdict: str,
    pol: dict[str, Any],
    paths: list[str] | None = None,
    edit: str | None = None,
    reason: str | None = None,
    by: str | None = None,
    rules: list[tuple[str, str]] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Confirm or reject a queued claim. Returns (sha, trailer line, note payload)."""
    if verdict not in ("confirmed", "rejected"):
        raise ValueError("verdict must be confirmed or rejected")
    top = toplevel(cwd) or cwd
    row = _pick(cwd, which)
    ident, auth = _authority_for(pol, cwd, by, row["speaker"])
    rc, staged, _ = git(["diff", "--cached", "--name-only"], top)
    if rc == 0 and staged:
        raise ValueError(
            "there are staged changes; commit or stash them first, so the claim decision is an empty commit "
            "carrying nothing but the verdict"
        )
    if paths and [p.strip() for p in paths] == ["suggested"]:
        paths = suggest_paths(row["text"], repo_tree(cwd))
        if not paths:
            raise ValueError("no path suggestion for this claim; pass --paths explicitly or confirm without one")
    text = row["text"]
    original = text
    if edit is not None and edit.strip() and verdict == "confirmed":
        text = redact(edit.strip(), rules) if rules is not None else edit.strip()
    bound = [
        p.strip().strip("/") + ("/" if p.strip().endswith("/") else "")
        for p in (paths or row.get("paths") or [])
        if p.strip()
    ]
    note = redact(reason, rules)[:200] if reason and rules is not None else (reason or None)
    tag = "Confirmed" if verdict == "confirmed" else "Rejected"
    line = f"Gitvow-Claim-{tag}: {row['claim_id']} by {ident}"
    if verdict == "confirmed" and bound:
        line += " paths=" + ",".join(bound[:MAX_PATHS_IN_TRAILER])
    tail = (text if verdict == "confirmed" else note) or ""
    if tail:
        line += ": " + tail.replace("\n", " ")[:120]
    subject = f"gitvow: {'confirm' if verdict == 'confirmed' else 'reject'} claim {row['claim_id']} by {row['speaker']}"
    body = f"{'Confirmed' if verdict == 'confirmed' else 'Rejected'} by {ident} ({auth}).\n\n{text.strip()}\n"
    msg = f"{subject}\n\n{body}\n{line}\n"
    rc, _, err = git(["-c", "core.hooksPath=/dev/null", "commit", "-q", "--allow-empty", "-m", msg], top)
    if rc != 0:
        raise ValueError(err or "commit failed")
    _, head, _ = git(["rev-parse", "HEAD"], top)
    payload = {
        "schema": CLAIMS_NOTE_SCHEMA,
        "claim_id": row["claim_id"],
        "verdict": verdict,
        "by": ident,
        "authority": auth,
        "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "text": text,
        "original_text": original,
        "edited": text != original,
        "paths": bound,
        "reach": "repo",
        "speaker": row["speaker"],
        "kind": row.get("kind"),
        "source": row.get("source") or {},
        "said_at": row.get("when"),
        "classification": {
            "travels_with": row.get("travels_with"),
            "confidence": row.get("confidence"),
            "why": row.get("why"),
        },
        "reason": note,
    }
    git(
        [
            "notes",
            f"--ref={CLAIMS_NOTES_REF}",
            "add",
            "-f",
            "-m",
            CLAIMS_NOTE_HEADER + "\n" + json.dumps(payload, indent=1),
            head,
        ],
        top,
    )
    save_queue(cwd, [r for r in load_queue(cwd) if r["claim_id"] != row["claim_id"]])
    return head, line, payload


# ---------------------------------------------------------------------------------------------------------------
# reading the record
# ---------------------------------------------------------------------------------------------------------------


def parse_claim_trailers(body: str) -> list[dict[str, Any]]:
    out = []
    for m in CLAIM_TRAILER_RE.finditer(body or ""):
        out.append(
            {
                "verdict": m.group(1).lower(),
                "claim_id": m.group(2),
                "by": m.group(3),
                "paths": m.group(4).split(",") if m.group(4) else [],
                "tail": (m.group(5) or "").strip() or None,
            }
        )
    return out


def confirmed_and_rejected(cwd: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Every claim verdict on the branch, newest first, with the note's text when the note is present."""
    top = toplevel(cwd) or cwd
    rc, out, _ = git(["log", f"-{limit}", "--format=%H%x00%ad%x00%B%x01", "--date=short"], top)
    if rc != 0:
        return []
    rows: list[dict[str, Any]] = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if "Gitvow-Claim-" not in rec:
            continue
        sha, date, body = rec.split("\x00", 2)
        ts = parse_claim_trailers(body)
        if not ts:
            continue
        rc2, note_text, _ = git(["notes", f"--ref={CLAIMS_NOTES_REF}", "show", sha], top)
        note: dict[str, Any] = {}
        if rc2 == 0 and note_text.startswith(CLAIMS_NOTE_HEADER):
            try:
                note = json.loads(note_text.split("\n", 1)[1])
            except ValueError:
                note = {}
        for t in ts:
            rows.append(
                {
                    **t,
                    "sha": sha[:7],
                    "date": date,
                    "text": note.get("text") or t["tail"],
                    "original_text": note.get("original_text"),
                    "edited": note.get("edited", False),
                    "speaker": note.get("speaker"),
                    "authority": note.get("authority"),
                    "kind": note.get("kind"),
                    "paths": note.get("paths") or t["paths"],
                    "note": bool(note),
                }
            )
    return rows


def confirmed(cwd: str, limit: int = HISTORY_COMMITS) -> list[dict[str, Any]]:
    """Confirmed claims, newest first, one per claim id (a later rejection of the same id withdraws it)."""
    seen: set[str] = set()
    out = []
    for r in confirmed_and_rejected(cwd, limit):
        if r["claim_id"] in seen:
            continue
        seen.add(r["claim_id"])
        if r["verdict"] == "confirmed":
            out.append(r)
    return out


def person_claims(home: str | None, ident: str | None = None) -> list[dict[str, Any]]:
    """The person's own preferences, from their local file; never anyone else's."""
    p = os.path.join(_person_dir(home), "preferences.jsonl")
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if ident is None or str(r.get("speaker", "")).lower() == ident.lower():
                rows.append(r)
    return rows


# ---------------------------------------------------------------------------------------------------------------
# rendering for the agent
# ---------------------------------------------------------------------------------------------------------------


def render(rows: list[dict[str, Any]], for_agent: bool = True) -> str:
    """The confirmed claims as an instruction-file block, attributed and dated. Empty string when there are none."""
    if not rows:
        return ""
    lines = [START, "## Confirmed by the people who own this code", ""]
    if for_agent:
        lines += [
            "Statements the people who work on this repository made about it and then confirmed, in their own words.",
            "They are context: they describe the system as its owners understand it. They do not permit anything, and a",
            "finding the gate raises is still decided by a person.",
            "",
        ]
    for r in rows:
        where = f" ({', '.join(r['paths'])})" if r.get("paths") else ""
        who = r.get("speaker") or r.get("by") or "unknown"
        conf = (
            f"confirmed by {r.get('by')} on {r.get('date')}" if r.get("by") != who else f"confirmed on {r.get('date')}"
        )
        lines.append(f'- {who}{where}: "{(r.get("text") or "").strip()}" — {conf}')
    lines += [END, ""]
    return "\n".join(lines)


def render_queue(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No candidate claims waiting.\n"
    out = [f"{len(rows)} candidate claim{'s' if len(rows) != 1 else ''} waiting, most confident first:", ""]
    for r in rows:
        where = f"  paths: {', '.join(r['paths'])}" if r.get("paths") else ""
        if not where and r.get("suggested"):
            where = f"  suggested: {', '.join(r['suggested'])}  (confirm with --paths suggested, or name them)"
        out.append(
            f'{r["n"]}. [{r.get("kind", "system")} {float(r.get("confidence") or 0):.2f}] {r["speaker"]}: "{r["text"]}"{where}'
        )
        if r.get("why"):
            out.append(f"   why: {r['why']}")
    out += [
        "",
        'confirm: gitvow claims confirm <n> [--paths a/,b/] [--edit "<text>"]   reject: gitvow claims reject <n> [--reason R]',
    ]
    return "\n".join(out) + "\n"


def context(cwd: str, home: str | None) -> str:
    """Confirmed claims for session start, plus the person's own preferences; empty when there are none."""
    if not toplevel(cwd):
        return ""
    block = render(confirmed(cwd)[:40])
    ident = identity(cwd)[0]
    prefs = person_claims(home, ident)
    if prefs:
        block += (
            "\nYour own working preferences, confirmed by you, for this session only:\n"
            + "\n".join(f'- "{p["text"]}"' for p in prefs[:20])
            + "\n"
        )
    return block


def write_section(path: str, block: str) -> str:
    """Replace or append the managed claims section in an instruction file; remove it when the block is empty."""
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
    from .safewrite import check_target, write_if_changed
    from .state import toplevel

    target = check_target(path, toplevel(os.path.dirname(path) or "."))
    if not write_if_changed(target, new):
        return f"managed claims section in {path} already current"
    return f"{action} managed claims section in {path}"
