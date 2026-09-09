"""Working-tree snapshots after agent edits: commits under refs/gitvow/snapshots/<session>/<n>, parented on HEAD."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
import time
from typing import Any

from .state import git, git_dir, load_state, log_event, save_state, toplevel

REF_PREFIX = "refs/gitvow/snapshots"
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_per_session": 200,
    "exclude": [".env*", "*.pem", "*.key", "*secret*", "*credential*", ".gitvow/**", ".claude/**"],
}


def settings(pol: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    out.update((pol or {}).get("snapshots") or {})
    return out


def _safe(session_id: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")


def ref_for(session_id: str | None, n: int) -> str:
    return f"{REF_PREFIX}/{_safe(session_id)}/{n}"


def take(cwd: str, session_id: str | None, tool: str, file_path: str, cfg: dict[str, Any]) -> str | None:
    """Write the working tree (minus ignored and excluded paths) as a commit and point a new session ref at it."""
    top = toplevel(cwd)
    gd = git_dir(cwd)
    if not top or not gd:
        return None
    rc, head, _ = git(["rev-parse", "--verify", "HEAD"], top)
    parent = head if rc == 0 else None
    st = load_state(cwd)
    n = int(st.get("snapshots", 0)) + 1
    fd, tmp_index = tempfile.mkstemp(prefix="gitvow-index-", dir=gd)
    os.close(fd)
    os.remove(tmp_index)
    env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
    try:
        if parent:
            subprocess.run(["git", "read-tree", parent], cwd=top, env=env, capture_output=True, check=False)
        pathspec = ["--", "."] + [f":(exclude,glob){p}" for p in cfg.get("exclude", [])]
        r = subprocess.run(
            ["git", "add", "-A", *pathspec], cwd=top, env=env, capture_output=True, text=True, timeout=60, check=False
        )
        if r.returncode != 0:
            return None
        r = subprocess.run(["git", "write-tree"], cwd=top, env=env, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            return None
        tree = r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp_index)
    rel = os.path.relpath(os.path.realpath(file_path), os.path.realpath(top)) if file_path else ""
    msg = json.dumps(
        {
            "gitvow": "snapshot",
            "session": session_id,
            "n": n,
            "tool": tool,
            "file": rel,
            "taken": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )
    # the snapshot is gitvow's object, not the user's commit: give it a fixed identity so it never depends on git config
    ident = {
        **os.environ,
        "GIT_AUTHOR_NAME": "gitvow",
        "GIT_AUTHOR_EMAIL": "gitvow@localhost",
        "GIT_COMMITTER_NAME": "gitvow",
        "GIT_COMMITTER_EMAIL": "gitvow@localhost",
    }
    args = ["git", "commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
    r = subprocess.run(args, cwd=top, env=ident, capture_output=True, text=True, check=False)
    if r.returncode != 0:
        log_event(cwd, "snapshot_failed", {"step": "commit-tree", "error": r.stderr.strip()[:300]})
        return None
    commit = r.stdout.strip()
    ref = ref_for(session_id, n)
    git(["update-ref", ref, commit], top)
    st["snapshots"] = n
    st["last_snapshot"] = ref
    save_state(cwd, st)
    keep = int(cfg.get("max_per_session", 200))
    if n > keep:
        git(["update-ref", "-d", ref_for(session_id, n - keep)], top)
    return ref


def list_snapshots(cwd: str, session_id: str | None = None) -> list[dict[str, Any]]:
    top = toplevel(cwd) or cwd
    prefix = f"{REF_PREFIX}/{_safe(session_id)}/" if session_id else REF_PREFIX + "/"
    rc, out, _ = git(["for-each-ref", "--format=%(refname)%09%(objectname)%09%(contents:subject)", prefix], top)
    rows: list[dict[str, Any]] = []
    if rc != 0:
        return rows
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        ref, commit, subject = parts
        try:
            meta = json.loads(subject)
        except json.JSONDecodeError:
            meta = {}
        _, stat, _ = git(["diff", "--shortstat", f"{commit}^", commit], top)
        m = re.match(r"\s*(\d+) file", stat or "")
        rows.append(
            {
                "ref": ref,
                "commit": commit,
                "session": meta.get("session") or ref.split("/")[-2],
                "n": int(meta.get("n") or ref.split("/")[-1]),
                "tool": meta.get("tool", ""),
                "file": meta.get("file", ""),
                "taken": meta.get("taken", ""),
                "changed_files": int(m.group(1)) if m else 0,
            }
        )
    rows.sort(key=lambda r: (r["session"], r["n"]))
    return rows


def resolve(cwd: str, session_prefix: str, n: int) -> str | None:
    """Full ref for a session id or unique prefix and a snapshot number."""
    top = toplevel(cwd) or cwd
    rc, out, _ = git(["for-each-ref", "--format=%(refname)", REF_PREFIX + "/"], top)
    if rc != 0:
        return None
    hits = {r.rsplit("/", 1)[0] for r in out.split() if r.split("/")[-2].startswith(_safe(session_prefix))}
    if len(hits) != 1:
        return None
    ref = f"{hits.pop()}/{n}"
    rc, _, _ = git(["rev-parse", "--verify", "--quiet", ref], top)
    return ref if rc == 0 else None


def diff(cwd: str, ref: str, full: bool = False) -> str:
    top = toplevel(cwd) or cwd
    args = ["diff"] + ([] if full else ["--stat"]) + [f"{ref}^", ref]
    rc, out, err = git(args, top)
    return out if rc == 0 else err


def restore(cwd: str, ref: str, to: str | None = None) -> tuple[int, str]:
    top = toplevel(cwd) or cwd
    if not to:
        sess, n = ref.split("/")[-2], ref.split("/")[-1]
        to = os.path.join(tempfile.gettempdir(), "gitvow-restore", f"{sess[:8]}-{n}")
    if os.path.exists(to) and os.listdir(to):
        return 1, f"{to} exists and is not empty; choose another --to"
    os.makedirs(os.path.dirname(to) or ".", exist_ok=True)
    rc, _, err = git(["worktree", "add", "--detach", to, ref], top)
    if rc != 0:
        return 1, err
    log_event(top, "restore", {"ref": ref, "to": to})
    return 0, to


def prune(cwd: str, older_than_days: float | None = None, session_id: str | None = None) -> int:
    top = toplevel(cwd) or cwd
    cutoff = time.time() - (older_than_days or 0) * 86400 if older_than_days is not None else None
    removed = 0
    for row in list_snapshots(cwd, session_id):
        if cutoff is not None:
            try:
                taken = time.mktime(time.strptime(row["taken"], "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, TypeError):
                taken = 0
            if taken > cutoff:
                continue
        git(["update-ref", "-d", row["ref"]], top)
        removed += 1
    return removed


def purge_all(cwd: str) -> int:
    return prune(cwd, None, None)
