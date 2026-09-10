"""Is the record actually being written here?

`gitvow status` checks what install left behind and what each agent still needs before the gate is live.
Every failure prints the one command or line that fixes it. A gate that is installed but inert is the
worst outcome, so this exists to make that state loud.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import Any

from .install import AGENT_FILES, MARKER, NOTES_GLOB, agent_next_steps
from .policy import PolicyError, load_policy
from .redact import RedactionError, load_rules
from .state import git, git_dir, toplevel

GIT_HOOKS = ("prepare-commit-msg", "pre-commit", "post-commit", "pre-push")
Check = tuple[str, str, str]  # (state: ok|fail|note, what, fix or detail)


def _hook_commands(path: str) -> list[str]:
    """Every gitvow hook command in an agent settings file, whatever the schema around it."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    out: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            cmd = node.get("command")
            if isinstance(cmd, str) and MARKER in cmd:
                out.append(cmd)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out


def _executable(cmd: str) -> tuple[bool, str, bool]:
    """(runnable, resolved path, found on PATH rather than recorded absolutely)."""
    parts = cmd.split()
    exe = parts[0] if parts else ""
    if exe and os.path.isabs(exe):
        return os.access(exe, os.X_OK), exe, False
    found = shutil.which(exe) if exe else None
    return bool(found), found or exe, True


def _agent_checks(home: str, cwd: str) -> list[Check]:
    checks: list[Check] = []
    top = toplevel(cwd)
    found_any = False
    for agent, (user_rel, repo_rel) in AGENT_FILES.items():
        for scope, path in (("user", os.path.join(home, user_rel)), ("repo", os.path.join(top or cwd, repo_rel))):
            if scope == "repo" and not top:
                continue
            cmds = _hook_commands(path)
            if not cmds:
                continue
            found_any = True
            where = path.replace(home, "~", 1)
            runnable, resolved, from_path = _executable(cmds[0])
            if not runnable:
                checks.append(
                    (
                        "fail",
                        f"{agent} ({scope}): hook command cannot be run: {cmds[0].split()[0]}",
                        f"re-run `gitvow install {'--user ' if scope == 'user' else ''}--agent {agent}`, "
                        "which records the absolute path of this install",
                    )
                )
            elif from_path:
                checks.append(
                    (
                        "note",
                        f"{agent} ({scope}): hook runs `{cmds[0].split()[0]}` from PATH → {resolved}",
                        "a desktop agent started from Finder or Spotlight may not have your shell's PATH; "
                        "an absolute path is safer",
                    )
                )
            else:
                checks.append(("ok", f"{agent} ({scope}): {len(cmds)} hooks in {where} → {resolved}", ""))
            checks += _agent_notes(agent, home, top)
    if not found_any:
        checks.append(
            ("fail", "no agent has gitvow hooks installed", "run `gitvow install --user` (add --agent for others)")
        )
    return checks


def _agent_notes(agent: str, home: str, top: str | None) -> list[Check]:
    """The same per-agent steps install prints, restated as checks."""
    out: list[Check] = []
    for step in agent_next_steps(agent, home, top):
        if step.startswith("check it with"):
            continue
        what, _, fix = step.partition(". ")
        state = "fail" if "switched off" in step else "note"
        out.append((state, what, fix.strip()))
    return out


def _git_checks(cwd: str, home: str) -> list[Check]:
    top = toplevel(cwd)
    if not top:
        return [("note", "not inside a git repository", "run this in a repository to check its git hooks")]
    checks: list[Check] = []
    rc, hp, _ = git(["config", "--get", "core.hooksPath"], top)
    if rc != 0 or not hp:
        checks.append(("fail", "core.hooksPath is not set, so no commit gets a trailer", "run `gitvow install --user`"))
        return checks
    hooks_dir = hp if os.path.isabs(hp) else os.path.join(top, hp)
    missing = [h for h in GIT_HOOKS if not os.access(os.path.join(hooks_dir, h), os.X_OK)]
    if missing:
        checks.append(
            (
                "fail",
                f"git hooks missing or not executable in {hp}: {', '.join(missing)}",
                "re-run `gitvow install` here; 0.12 added pre-commit and post-commit",
            )
        )
    else:
        checks.append(("ok", f"git hooks in {hp}: {', '.join(GIT_HOOKS)}", ""))
    rc, refs, _ = git(["config", "--get-all", "notes.displayRef"], top)
    if NOTES_GLOB not in (refs or ""):
        checks.append(
            (
                "note",
                "notes.displayRef does not list the session notes",
                f"`git config --add notes.displayRef {NOTES_GLOB}`",
            )
        )
    else:
        checks.append(("ok", f"notes.displayRef / rewriteRef → {NOTES_GLOB}", ""))
    gd = git_dir(top)
    if gd and os.path.exists(os.path.join(gd, "gitvow-hooks.log")):
        checks.append(("ok", "this repository has a hook log, so the hooks have run here", ""))
    else:
        checks.append(("note", "no hook log in this repository yet, so no session has been recorded here", ""))
    return checks


def build(cwd: str, home: str) -> list[Check]:
    checks: list[Check] = []
    try:
        pol = load_policy(cwd, home)
        n = sum(len(pol.get(k, [])) for k in ("bash_deny", "bash_confirm", "path_confirm"))
        checks.append(("ok", f"policy loads: {n} rules, {len(pol.get('providers') or [])} providers", ""))
    except PolicyError as e:
        checks.append(("fail", f"policy will not load, so every tool call is refused: {e}", "fix or restore the file"))
    try:
        load_rules(cwd, home)
        checks.append(("ok", "redaction rules load", ""))
    except RedactionError as e:
        checks.append(("fail", f"redaction rules invalid, so nothing is written: {e}", "fix the rules file"))
    checks += _agent_checks(home, cwd)
    checks += _git_checks(cwd, home)
    return checks


def render(checks: list[Check]) -> str:
    from . import __version__

    mark = {"ok": "  ok   ", "fail": "  FAIL ", "note": "  note "}
    lines = [f"gitvow {__version__}", ""]
    for state, what, fix in checks:
        lines.append(mark[state] + what)
        if fix:
            lines.append("         " + fix)
    fails = sum(1 for s, _, _ in checks if s == "fail")
    notes = sum(1 for s, _, _ in checks if s == "note")
    lines += [
        "",
        f"{fails} failing, {notes} to check by hand." if fails else f"nothing failing, {notes} to check by hand.",
    ]
    return "\n".join(lines) + "\n"
