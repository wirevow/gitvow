"""Install/uninstall into Claude Code settings and git hooks, per user or per repo. Idempotent; removes only what it added."""

from __future__ import annotations

import json
import os
import shutil
import sys
from typing import Any

from .state import git

GIT_HOOK = """#!/bin/sh
# gitvow: append session trailers when a session is active in this repo; chain to the repo's own hook if present.
GD="$(git rev-parse --git-dir)"; STATE="$GD/gitvow-session.json"
if [ -f "$STATE" ]; then
  # Trailers go only on commits the agent itself runs: the PreToolUse gate sets pending_commit when the
  # agent invokes git commit and PostToolUse clears it. A human commit in a terminal gets no trailer.
  SID=$(python3 -c "import json,time;s=json.load(open('$STATE'));p=s.get('pending_commit') or 0;print(s.get('session_id') or '' if time.time()-p<300 else '')" 2>/dev/null)
  STEP=$(python3 -c "import json;print(json.load(open('$STATE')).get('steps') or 0)" 2>/dev/null)
  if [ -n "$SID" ] && ! grep -q "^Gitvow-Session:" "$1"; then printf "\\nGitvow-Session: %s\\nGitvow-Step: %s\\n" "$SID" "$STEP" >> "$1"; fi
fi
SELF="$(cd "$(dirname "$0")" && pwd)"; REPOHOOKS="$(cd "$GD/hooks" 2>/dev/null && pwd || true)"
[ -x "$GD/hooks/prepare-commit-msg" ] && [ "$SELF" != "$REPOHOOKS" ] && exec "$GD/hooks/prepare-commit-msg" "$@"
exit 0
"""
MARKER = "gitvow hook "
NOTES_GLOB = "refs/notes/gitvow/*"
NOTES_KEYS = ("notes.displayRef", "notes.rewriteRef")


def _set_notes_config(cwd: str, scope: list[str]) -> None:
    for key in NOTES_KEYS:
        rc, cur, _ = git(["config", *scope, "--get-all", key], cwd)
        if rc != 0 or NOTES_GLOB not in cur.splitlines():
            git(["config", *scope, "--add", key, NOTES_GLOB], cwd)


def _unset_notes_config(cwd: str, scope: list[str]) -> None:
    for key in NOTES_KEYS:
        git(["config", *scope, "--unset", key, "^" + NOTES_GLOB.replace("*", "\\*").replace(".", "\\.") + "$"], cwd)


def executable_command() -> str:
    """The command Claude Code should run for this install of gitvow.

    Hooks run in a shell that may not have a virtualenv or pipx path activated, and a hook that
    cannot be found exits non-zero without blocking, which would silently switch the gate off.
    A per-user install therefore records the absolute path of the console script when one exists,
    falling back to `<python> -m gitvow`; per-repo installs keep the bare name because the settings
    file is shared by people with different paths.
    """
    exe = shutil.which("gitvow", path=os.path.dirname(sys.executable)) or shutil.which("gitvow")
    if exe:
        return exe
    return f"{sys.executable} -m gitvow"


def _hook_entries(cmd_prefix: str) -> dict[str, list[dict[str, Any]]]:
    def entry(event: str, matcher: str | None) -> dict[str, Any]:
        e: dict[str, Any] = {"hooks": [{"type": "command", "command": f"{cmd_prefix} hook {event}"}]}
        if matcher:
            e["matcher"] = matcher
        return e

    return {
        "SessionStart": [entry("SessionStart", None)],
        "PreToolUse": [entry("PreToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit|mcp__.*")],
        "PostToolUse": [entry("PostToolUse", "Bash|Edit|Write|MultiEdit|NotebookEdit")],
        "Stop": [entry("Stop", None)],
    }


def merge_settings(path: str, cmd_prefix: str) -> None:
    cur: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as fh:
            cur = json.load(fh)
    hooks = cur.setdefault("hooks", {})
    for ev, entries in _hook_entries(cmd_prefix).items():
        kept = [x for x in hooks.get(ev, []) if not any(MARKER in (h.get("command") or "") for h in x.get("hooks", []))]
        hooks[ev] = kept + entries
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(cur, fh, indent=2)


def unmerge_settings(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as fh:
        cur = json.load(fh)
    hooks = cur.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [x for x in hooks[ev] if not any(MARKER in (h.get("command") or "") for h in x.get("hooks", []))]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        cur.pop("hooks", None)
    if cur:
        with open(path, "w") as fh:
            json.dump(cur, fh, indent=2)
    else:
        os.remove(path)


def _write_git_hook(dirpath: str) -> str:
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "prepare-commit-msg")
    with open(p, "w") as fh:
        fh.write(GIT_HOOK)
    os.chmod(p, 0o755)  # noqa: S103  # nosec B103 - git runs hooks as the invoking user; must be executable
    return p


def install_user(home: str, cmd_prefix: str | None = None) -> list[str]:
    base = os.path.join(home, ".gitvow")
    os.makedirs(base, exist_ok=True)
    cmd_prefix = cmd_prefix or executable_command()
    done = [f"hook command → {cmd_prefix}"]
    pol = os.path.join(base, "policy.json")
    if not os.path.exists(pol):
        from .policy import DEFAULT_POLICY_PATH

        shutil.copy(DEFAULT_POLICY_PATH, pol)
        done.append(f"default policy → {pol}")
    _write_git_hook(os.path.join(base, "git-hooks"))
    merge_settings(os.path.join(home, ".claude", "settings.json"), cmd_prefix)
    git(["config", "--global", "core.hooksPath", os.path.join(base, "git-hooks")], home)
    _set_notes_config(home, ["--global"])
    done += [
        "hooks merged into ~/.claude/settings.json",
        "global core.hooksPath → ~/.gitvow/git-hooks",
        f"global notes.displayRef / notes.rewriteRef → {NOTES_GLOB}",
    ]
    return done


def uninstall_user(home: str, purge_policy: bool = False, purge_ledger: bool = False) -> list[str]:
    base = os.path.join(home, ".gitvow")
    done = []
    unmerge_settings(os.path.join(home, ".claude", "settings.json"))
    rc, cur, _ = git(["config", "--global", "--get", "core.hooksPath"], home)
    if rc == 0 and cur == os.path.join(base, "git-hooks"):
        git(["config", "--global", "--unset", "core.hooksPath"], home)
        done.append("global core.hooksPath unset")
    shutil.rmtree(os.path.join(base, "git-hooks"), ignore_errors=True)
    _unset_notes_config(home, ["--global"])
    if purge_policy and os.path.exists(os.path.join(base, "policy.json")):
        os.remove(os.path.join(base, "policy.json"))
        done.append("policy removed")
    if purge_ledger:
        shutil.rmtree(os.path.join(base, "ledger"), ignore_errors=True)
        done.append("ledger removed")
    done.append("hook entries removed from ~/.claude/settings.json")
    return done


def install_repo(repo: str, cmd_prefix: str = "gitvow") -> list[str]:
    base = os.path.join(repo, ".gitvow")
    os.makedirs(base, exist_ok=True)
    from .policy import DEFAULT_POLICY_PATH

    pol = os.path.join(base, "policy.json")
    if not os.path.exists(pol):
        shutil.copy(DEFAULT_POLICY_PATH, pol)
    _write_git_hook(os.path.join(base, "git-hooks"))
    merge_settings(os.path.join(repo, ".claude", "settings.json"), cmd_prefix)
    git(["config", "core.hooksPath", ".gitvow/git-hooks"], repo)
    _set_notes_config(repo, [])
    return [
        f"policy → {pol}",
        "hooks merged into .claude/settings.json",
        "core.hooksPath → .gitvow/git-hooks",
        f"notes.displayRef / notes.rewriteRef → {NOTES_GLOB}",
        "commit .gitvow/ and .claude/settings.json to share; teammates run: git config core.hooksPath .gitvow/git-hooks",
    ]


def uninstall_repo(repo: str, purge_notes: bool = False) -> list[str]:
    done = []
    unmerge_settings(os.path.join(repo, ".claude", "settings.json"))
    rc, cur, _ = git(["config", "--get", "core.hooksPath"], repo)
    if rc == 0 and cur == ".gitvow/git-hooks":
        git(["config", "--unset", "core.hooksPath"], repo)
        done.append("core.hooksPath unset")
    shutil.rmtree(os.path.join(repo, ".gitvow"), ignore_errors=True)
    _unset_notes_config(repo, [])
    rc, gd, _ = git(["rev-parse", "--git-dir"], repo)
    if rc == 0:
        gd = gd if os.path.isabs(gd) else os.path.join(repo, gd)
        for f in ("gitvow-session.json", "gitvow-hooks.log"):
            p = os.path.join(gd, f)
            if os.path.exists(p):
                os.remove(p)
    if purge_notes:
        _, refs, _ = git(["for-each-ref", "--format=%(refname)", "refs/notes/sessions", "refs/notes/gitvow/"], repo)
        for ref in refs.split():
            git(["update-ref", "-d", ref], repo)
        done.append(f"{len(refs.split())} local session note ref(s) deleted (remote copies untouched)")
    done.append(".gitvow removed; commit trailers already in history remain")
    return done
