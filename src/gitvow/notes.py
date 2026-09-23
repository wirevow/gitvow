"""Finding a commit's session note, with or without the trailer that names its session.

Readers used to look a note up by the session id in the commit's `Gitvow-Session:` trailer. A squash commit made
by a forge is a new commit whose message a person may have edited, so the trailer can be gone while the note,
carried onto the new commit by `gitvow carry`, is there under the session's ref. So: the named ref first, the
0.1 shared ref second, and then every `refs/notes/gitvow/*` ref for a note on this commit.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .state import git

PREFIX = "gitvow"
LEGACY = "sessions"


def ref_for(session_id: str | None) -> str:
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")
    return f"{PREFIX}/{sid}"


def _parse(body: str) -> dict[str, Any] | None:
    if not body.startswith("gitvow-session"):
        return None
    try:
        return json.loads(body.split("\n", 1)[1])
    except (json.JSONDecodeError, IndexError):
        return None


def session_refs(cwd: str) -> list[str]:
    rc, out, _ = git(["for-each-ref", "--format=%(refname)", f"refs/notes/{PREFIX}/"], cwd)
    return [r[len("refs/notes/") :] for r in out.split()] if rc == 0 else []


def find_note(cwd: str, sha: str, session_id: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """(note, ref) for `sha`: the session's own ref, the legacy ref, then a scan of every session ref."""
    tried: list[str] = []
    if session_id:
        tried.append(ref_for(session_id))
    tried.append(LEGACY)
    for ref in tried:
        rc, body, _ = git(["notes", f"--ref={ref}", "show", sha], cwd)
        if rc == 0:
            data = _parse(body)
            if data is not None:
                return data, ref
    for ref in session_refs(cwd):
        if ref in tried:
            continue
        rc, body, _ = git(["notes", f"--ref={ref}", "show", sha], cwd)
        if rc == 0:
            data = _parse(body)
            if data is not None:
                return data, ref
    return None, None
