"""Per-repository state kept inside .git (never in the tree) and the append-only hook log."""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any


def git(args: list[str], cwd: str) -> tuple[int, str, str]:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def git_dir(cwd: str) -> str | None:
    rc, out, _ = git(["rev-parse", "--git-dir"], cwd)
    if rc != 0:
        return None
    return out if os.path.isabs(out) else os.path.join(cwd, out)


def state_path(cwd: str) -> str | None:
    gd = git_dir(cwd)
    return os.path.join(gd, "gitvow-session.json") if gd else None


def load_state(cwd: str) -> dict[str, Any]:
    p = state_path(cwd)
    if p and os.path.exists(p):
        try:
            with open(p) as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(cwd: str, st: dict[str, Any]) -> None:
    p = state_path(cwd)
    if p:
        with open(p, "w") as fh:
            json.dump(st, fh, indent=1)


def log_event(cwd: str, kind: str, payload: dict[str, Any]) -> None:
    gd = git_dir(cwd)
    if not gd:
        return
    with open(os.path.join(gd, "gitvow-hooks.log"), "a") as fh:
        fh.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **payload}) + "\n")
