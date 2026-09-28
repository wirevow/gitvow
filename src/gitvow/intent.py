"""Intent: what a task is for, in the person's words, stated once at task start and carried on every commit.

The only record of what a change was meant to achieve used to be a commit message written after the fact, and
every judgment downstream (the card, the reviewer, the outcome) was made without it. The intent is one line,
kept in the session state (`.git/gitvow-session.json`, never in the tree), written by the prepare-commit-msg
hook as `Gitvow-Intent: <text> by <who>` on each commit the agent makes in the session, and copied into the
session note. It comes from one of two places:

- `gitvow intent "<text>"`: a person states it (source `stated`).
- the first message of a Claude Code session, when the policy allows it (source `prompt`, and the trailer says
  so): the person's own words, first line only, redacted, at most MAX_TEXT characters. Slash commands and
  one-word nudges are not an intent and are skipped.

Coverage is the experiment this utility exists for: whether asking once at task start removes more consequential
questions than gating each action does. A finding is *within* the stated intent when the words of its subject
appear in the intent. That is a term overlap, nothing cleverer, and it never answers a finding: it is recorded on
the finding, shown on the card, and counted by the digest so the ordering of the toolkit can be decided on data.
"""

from __future__ import annotations

import re
import time
from typing import Any

from .redact import redact
from .state import load_state, log_event, save_state

TRAILER = "Gitvow-Intent"
MAX_TEXT = 200
MIN_WORDS = 3
INTENT_RE = re.compile(rf"^{TRAILER}:\s*(?P<text>.+?)\s+by\s+(?P<by>\S+)(?:\s+source=(?P<source>\S+))?\s*$", re.M)
# Blocks a harness adds around or inside the person's message: pasted text, editor selections, system reminders.
# None of them are the person's words about the task.
_PASTED_RE = re.compile(r"<(pasted_content|system-reminder|ide_selection|ide_opened_file)[^>]*>.*?</\1[^>]*>", re.S)
_TAG_RE = re.compile(r"<[^>\n]{1,80}>")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
# Words that name the shape of a finding rather than its subject. "edit", "route", "in" are in every finding;
# matching them would make every intent cover everything.
_STOP_WORDS = (
    "a an and are as at be by for from i in into is it its of on or that the this to we with you your "
    "add change edit edits fix make please update use want need should could would will can "
    "file files path route command commit push pushing branch protected production cluster mutation "
    "src lib app main index test tests yaml yml json py js ts go md txt"
)
STOP = frozenset(_STOP_WORDS.split())


def settings(pol: dict[str, Any] | None) -> dict[str, Any]:
    cfg = (pol or {}).get("intent") or {}
    return {
        "from_prompt": bool(cfg.get("from_prompt", True)),
        "ask_when_missing": bool(cfg.get("ask_when_missing", True)),
    }


def from_prompt(prompt: str | None) -> str | None:
    """The intent a first message states, or None when the message is not one (a slash command, a nudge)."""
    if not prompt or not isinstance(prompt, str):
        return None
    text = _PASTED_RE.sub(" ", prompt)
    text = _TAG_RE.sub(" ", text)
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first or first.startswith("/") or first.startswith("!"):
        return None
    first = " ".join(first.split())
    if len(first.split()) < MIN_WORDS:
        return None
    return first[:MAX_TEXT]


def current(cwd: str, session_id: str | None = None) -> dict[str, Any] | None:
    """The intent recorded for this repository's session, or None. With `session_id`, only if it is that session's."""
    st = load_state(cwd)
    i = st.get("intent")
    if not i or not i.get("text"):
        return None
    if session_id and i.get("session_id") and i["session_id"] != session_id:
        return None
    return i


def record(
    cwd: str,
    text: str,
    by: str,
    source: str,
    rules: list[tuple[str, str]] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Set the session's intent. Redacted, one line, at most MAX_TEXT characters. Replaces any earlier one."""
    clean = " ".join((redact(text, rules) if rules is not None else text).split())[:MAX_TEXT]
    if not clean:
        raise ValueError("an intent needs some words")
    st = load_state(cwd)
    i = {
        "text": clean,
        "by": by,
        "source": source,
        "set_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": session_id or st.get("session_id"),
        "step": st.get("steps", 0),
    }
    st["intent"] = i
    save_state(cwd, st)
    log_event(cwd, "intent", {"source": source, "by": by, "session_id": i["session_id"], "chars": len(clean)})
    return i


def clear(cwd: str) -> bool:
    st = load_state(cwd)
    had = bool(st.pop("intent", None))
    if had:
        save_state(cwd, st)
        log_event(cwd, "intent_cleared", {"session_id": st.get("session_id")})
    return had


def propagate(session_cwd: str, target_cwd: str) -> None:
    """The session's intent follows the session into another repository it reaches (0.25 routing).

    The intent lives in the state of the repository the session started in; a commit made through `git -C` in a
    second repository reads that repository's state. Copy the intent across, once, when the target has none or has
    one from a different session.
    """
    if session_cwd == target_cwd:
        return
    src = current(session_cwd)
    if not src:
        return
    st = load_state(target_cwd)
    have = st.get("intent") or {}
    if have.get("text") and have.get("session_id") == src.get("session_id"):
        return
    st["intent"] = dict(src)
    save_state(target_cwd, st)


def trailer_line(i: dict[str, Any]) -> str:
    line = f"{TRAILER}: {' '.join(str(i.get('text') or '').split())} by {i.get('by') or 'unknown'}"
    if i.get("source") == "prompt":
        line += " source=prompt"
    return line


def parse_trailer(body: str) -> dict[str, Any] | None:
    m = INTENT_RE.search(body or "")
    if not m:
        return None
    return {"text": m.group("text"), "by": m.group("by"), "source": m.group("source") or "stated"}


def terms(text: str) -> set[str]:
    """Words worth matching: split on punctuation and camelCase, lowercased, three letters or more, not STOP."""
    out: set[str] = set()
    for w in _SPLIT_RE.split(_CAMEL_RE.sub(" ", text or "")):
        w = w.lower()
        if len(w) >= 3 and w not in STOP and not w.isdigit():
            out.add(w)
    return out


def covers(intent_text: str, finding: dict[str, Any]) -> bool:
    """Whether the finding's subject shares a word with the intent. A term overlap, never an answer."""
    want = terms(intent_text)
    if not want:
        return False
    subject = " ".join(str(finding.get(k) or "") for k in ("subject", "path", "finding"))
    return bool(want & terms(subject))


def mark_coverage(cwd: str) -> int:
    """Record on each open finding whether the stated intent covers it. Returns how many it covers."""
    st = load_state(cwd)
    i = st.get("intent") or {}
    n = 0
    changed = False
    for f in st.get("findings") or []:
        c = bool(i.get("text")) and covers(i["text"], f)
        if f.get("intent_covered") != c:
            f["intent_covered"] = c
            changed = True
        n += 1 if c and not f.get("decision") else 0
    if changed:
        save_state(cwd, st)
    return n


def card_header(i: dict[str, Any] | None) -> list[str]:
    if not i or not i.get("text"):
        return []
    how = "from the first message of the session" if i.get("source") == "prompt" else f"stated by {i.get('by')}"
    return [
        f'Stated intent: "{i["text"]}" ({how}).',
        "A finding marked within the intent shares its words; that is context for the answer, not the answer.",
        "",
    ]


def context(cwd: str, pol: dict[str, Any] | None, session_id: str | None = None) -> str:
    """One paragraph for the agent at session start: the intent if there is one, else how to get one."""
    cfg = settings(pol)
    i = current(cwd, session_id)
    if i:
        return (
            f'\ngitvow intent for this session: "{i["text"]}" ({i.get("by")}, {i.get("source")}). '
            "It rides on every commit as Gitvow-Intent. If the task changes, record the new intent with "
            'gitvow intent "<the person\'s words>".\n'
        )
    if not cfg["ask_when_missing"]:
        return ""
    first = (
        "gitvow records the first line of the person's first message as the intent of this session, in their words, "
        "and it rides on every commit as Gitvow-Intent. "
        if cfg["from_prompt"]
        else ""
    )
    return (
        f"\n{first}If that message does not say what the task is for, ask once, in one line, before the first edit, "
        'and record their answer verbatim with: gitvow intent "<their words>". Never invent one.\n'
    )
