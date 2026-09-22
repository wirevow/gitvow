"""Writes into a repository that a hostile checkout cannot redirect.

`install`, the rules block and the claims block all write files a repository can pre-seed: `.claude/settings.json`,
`.gitvow/git-hooks/*`, `CLAUDE.md`, `AGENTS.md`. A committed symlink at one of those paths could aim the write at
`.git/config`, a hook, or a file outside the checkout. Every such write goes through `check_target` first, and
through `write_if_changed`, so a rerun that changes nothing leaves the file's bytes and its mtime alone (the
install stamp `status` reads is a hook's mtime, so a rewrite would hide agent commits made before the rerun).
"""

from __future__ import annotations

import os
import stat


class UnsafeTargetError(Exception):
    """The write was refused; the message says why and nothing was written."""


def _real(p: str) -> str:
    return os.path.realpath(p)


def _inside(child: str, parent: str) -> bool:
    child, parent = _real(child), _real(parent)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _git_dir_of(root: str) -> str | None:
    from .state import git_dir

    return git_dir(root)


def check_target(path: str, root: str | None = None) -> str:
    """Refuse a target that is not an ordinary file location inside `root` (when given).

    Refused: a path whose resolution leaves `root`; a target inside the repository's git directory (recognised
    through `git rev-parse --git-dir`, so a relocated one counts); a target that exists and is not a regular
    file; a regular file with more than one name (hard link), because an inode's other names cannot be read back
    and one of them may be `.git/config`. Returns the resolved path to write.
    """
    resolved = _real(path)
    if root is not None:
        if not _inside(resolved, root):
            raise UnsafeTargetError(f"{path} resolves outside {root}; not written")
        gd = _git_dir_of(root)
        if gd and _inside(resolved, gd):
            raise UnsafeTargetError(f"{path} resolves into the git directory ({gd}); not written")
    try:
        st = os.lstat(resolved)
    except FileNotFoundError:
        return resolved
    if not stat.S_ISREG(st.st_mode):
        raise UnsafeTargetError(f"{path} exists and is not a regular file; not written")
    if st.st_nlink > 1:
        raise UnsafeTargetError(f"{path} has {st.st_nlink} names (hard link); not written")
    return resolved


def write_if_changed(path: str, content: str, mode: int | None = None) -> bool:
    """Write `content` to `path` only when the bytes differ. Returns True when a write happened."""
    try:
        with open(path) as fh:
            if fh.read() == content:
                if mode is not None and (os.stat(path).st_mode & 0o777) != mode:
                    os.chmod(path, mode)
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)
    if mode is not None:
        os.chmod(path, mode)
    return True
