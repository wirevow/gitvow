"""Is the record actually being written here?

`gitvow status` checks what install left behind and what each agent still needs before the gate is live.
Every failure prints the one command or line that fixes it. A gate that is installed but inert is the
worst outcome, so this exists to make that state loud.
"""

from __future__ import annotations

import os
import shutil
import time

from .install import (
    AGENT_FILES,
    GIT_HOOKS_EXPECTED,
    NOTES_GLOB,
    agent_next_steps,
    expected_events,
    hook_commands,
    stale_git_hooks,
)
from .policy import PolicyError, load_policy
from .redact import RedactionError, load_rules
from .state import git, git_dir, toplevel

Check = tuple[str, str, str]  # (state: ok|fail|note, what, fix or detail)


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
            cmds = hook_commands(path)
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
            missing = sorted(e for e in expected_events(agent) if not any(f" {e}" in c for c in cmds))
            if missing:
                checks.append(
                    (
                        "fail",
                        f"{agent} ({scope}): installed by an older gitvow, missing {', '.join(missing)}",
                        f"re-run `gitvow install {'--user ' if scope == 'user' else ''}--agent {agent}`",
                    )
                )
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
        if "restart it if a session is already open" in step:
            continue  # tested for directly below, per repository, from the commits themselves
        what, _, fix = step.partition(". ")
        state = "fail" if "switched off" in step else "note"
        out.append((state, what, fix.strip()))
    return out


def _since_install_checks(cwd: str, hooks_dir: str) -> list[Check]:
    """Has an agent committed here since gitvow was installed, without being recorded?

    This is the question process-sniffing tries to answer badly. A commit made after the install that
    carries an agent's signature but no session means the gate is not firing right now: usually a session
    that was already open when gitvow went in, since agents read hooks at start-up.
    """
    stamp = os.path.join(hooks_dir, "prepare-commit-msg")
    try:
        installed = os.path.getmtime(stamp)
    except OSError:
        return []
    stamp_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(installed))
    try:
        from .scan import coverage

        d = coverage(cwd, since=stamp_iso)
    except (ValueError, OSError):
        return []
    if not d["signed"] or not d["uncovered"]:
        return []
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(installed))
    holes = ", ".join(h["sha"] for h in d["holes"][:4])
    return [
        (
            "fail",
            f"{d['uncovered']} agent commit{'s' if d['uncovered'] != 1 else ''} since the install "
            f"({when}) {'carry' if d['uncovered'] != 1 else 'carries'} no session: {holes}",
            "an agent session that was already open when you installed is not gated, because hooks are "
            "read at start-up. Restart it, then `gitvow coverage` to confirm. Cursor reloads by itself.",
        )
    ]


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
    missing = [h for h in GIT_HOOKS_EXPECTED if not os.access(os.path.join(hooks_dir, h), os.X_OK)]
    if missing:
        checks.append(
            (
                "fail",
                f"git hooks missing or not executable in {hp}: {', '.join(missing)}",
                "re-run `gitvow install` here; 0.12 added pre-commit and post-commit",
            )
        )
    else:
        stale = stale_git_hooks(hooks_dir)
        if stale:
            checks.append(
                (
                    "fail",
                    f"git hooks written by an older gitvow: {', '.join(stale)}",
                    "re-run `gitvow install` here; behaviour added since that install is not active",
                )
            )
        else:
            checks.append(("ok", f"git hooks in {hp}: {', '.join(GIT_HOOKS_EXPECTED)}", ""))
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
    checks += _since_install_checks(top, hooks_dir)
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
    notes = sum(1 for s, _, fix in checks if s == "note" and fix)  # only notes that ask something of you
    tail = f" {notes} step{'s' if notes != 1 else ''} left to you above." if notes else ""
    if fails:
        lines += ["", f"{fails} failing: the record is not being written until those are fixed.{tail}"]
    elif any(s == "note" and "no hook log" in w for s, w, _ in checks):
        lines += ["", f"Ready: run a session and commit, or `gitvow selftest` to see it work now.{tail}"]
    else:
        lines += ["", f"The record is live here.{tail}"]
    return "\n".join(lines) + "\n"
