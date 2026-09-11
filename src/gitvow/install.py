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
  # Decisions recorded with `gitvow decide` become Gitvow-Accepted/Declined/Referred trailers; findings nobody
  # decided become Gitvow-Open, on agent and human commits alike. A referral is its own trailer because "the
  # question reached the wrong person" is not an answer and must never be read as one.
  if ! grep -q "^Gitvow-\\(Accepted\\|Declined\\|Open\\|Referred\\):" "$1"; then
    python3 - "$STATE" "$1" <<'PY' 2>/dev/null
import json, sys
KEYS = {"accepted": "Gitvow-Accepted: ", "declined": "Gitvow-Declined: ", "referred": "Gitvow-Referred: "}
st = json.load(open(sys.argv[1])); out = []
for f in st.get("findings") or []:
    d = f.get("decision")
    if not d:
        out.append("Gitvow-Open: " + f["finding"]); continue
    line = KEYS[d["answer"]] + f["finding"] + " by " + str(d["by"])
    if d.get("scope"): line += " scope=" + d["scope"]
    if d.get("to"): line += " to=" + d["to"]
    if d.get("note"): line += ": " + d["note"]
    out.append(line)
if out:
    msg = open(sys.argv[2]).read()
    with open(sys.argv[2], "a") as fh:
        fh.write(("" if msg.endswith("\\n") else "\\n") + ("\\n" if "Gitvow-Session:" not in msg else "") + "\\n".join(out) + "\\n")
PY
  fi
fi
SELF="$(cd "$(dirname "$0")" && pwd)"; REPOHOOKS="$(cd "$GD/hooks" 2>/dev/null && pwd || true)"
[ -x "$GD/hooks/prepare-commit-msg" ] && [ "$SELF" != "$REPOHOOKS" ] && exec "$GD/hooks/prepare-commit-msg" "$@"
exit 0
"""
PRE_PUSH_HOOK = """#!/bin/sh
# gitvow: push session notes (refs/notes/gitvow/*) to the same remote whenever a branch is pushed.
# The inner push re-enters this hook; GITVOW_PUSHING_NOTES stops the recursion.
REMOTE="$1"
if [ -z "$GITVOW_PUSHING_NOTES" ] && [ -n "$REMOTE" ] && git for-each-ref --count=1 refs/notes/gitvow/ | grep -q .; then
  GITVOW_PUSHING_NOTES=1 git push --quiet "$REMOTE" 'refs/notes/gitvow/*:refs/notes/gitvow/*' 2>/dev/null || true
fi
GD="$(git rev-parse --git-dir)"; SELF="$(cd "$(dirname "$0")" && pwd)"; REPOHOOKS="$(cd "$GD/hooks" 2>/dev/null && pwd || true)"
[ -x "$GD/hooks/pre-push" ] && [ "$SELF" != "$REPOHOOKS" ] && exec "$GD/hooks/pre-push" "$@"
exit 0
"""
PRE_COMMIT_HOOK = """#!/bin/sh
# gitvow: a person committing while the agent's findings are open. Default: say so and let the commit through
# (the trailer hook records Gitvow-Open). Policy decisions.mode "strict": refuse until every finding is decided.
GD="$(git rev-parse --git-dir)"; STATE="$GD/gitvow-session.json"; TOP="$(git rev-parse --show-toplevel)"
if [ -f "$STATE" ]; then
  python3 - "$STATE" "$TOP" <<'PY'
import json, os, sys, time
st = json.load(open(sys.argv[1]))
open_ = [f for f in st.get("findings") or [] if not f.get("decision")]
if not open_:
    sys.exit(0)
fresh = time.time() - (st.get("pending_commit") or 0) < 300  # the agent's own commit: the gate already asked
if fresh:
    sys.exit(0)
mode = "open"
for p in (os.path.join(sys.argv[2], ".gitvow", "policy.json"), os.path.expanduser("~/.gitvow/policy.json")):
    if os.path.exists(p):
        try:
            mode = (json.load(open(p)).get("decisions") or {}).get("mode", "open")
        except Exception:
            pass
        break
n = len(open_)
sys.stderr.write("gitvow: %d open finding%s from an agent session:\\n" % (n, "" if n == 1 else "s"))
for i, f in enumerate(open_, 1):
    sys.stderr.write("  %d. %s\\n     why: %s\\n" % (i, f["finding"], f.get("reason", "")))
if mode == "strict":
    sys.stderr.write("decisions.mode is strict: record an answer with `gitvow decide <n> accept|decline`, then commit again.\\n")
    sys.exit(1)
sys.stderr.write("recorded as Gitvow-Open on this commit; `gitvow decide` closes them.\\n")
PY
  [ $? -ne 0 ] && exit 1
fi
SELF="$(cd "$(dirname "$0")" && pwd)"; REPOHOOKS="$(cd "$GD/hooks" 2>/dev/null && pwd || true)"
[ -x "$GD/hooks/pre-commit" ] && [ "$SELF" != "$REPOHOOKS" ] && exec "$GD/hooks/pre-commit" "$@"
exit 0
"""
POST_COMMIT_HOOK = """#!/bin/sh
# gitvow: the commit now carries the findings; move them out of the open list so the note can record them
# and the next commit starts clean.
GD="$(git rev-parse --git-dir)"; STATE="$GD/gitvow-session.json"
if [ -f "$STATE" ]; then
  HEAD="$(git rev-parse HEAD 2>/dev/null)"
  python3 - "$STATE" "$HEAD" <<'PY' 2>/dev/null
import json, sys, time
st = json.load(open(sys.argv[1]))
fs = st.get("findings") or []
if fs:
    st["last_commit"] = {"sha": sys.argv[2], "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "findings": fs}
    st["findings"] = []
    json.dump(st, open(sys.argv[1], "w"), indent=1)
PY
fi
SELF="$(cd "$(dirname "$0")" && pwd)"; REPOHOOKS="$(cd "$GD/hooks" 2>/dev/null && pwd || true)"
[ -x "$GD/hooks/post-commit" ] && [ "$SELF" != "$REPOHOOKS" ] && exec "$GD/hooks/post-commit" "$@"
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


AGENT_FILES = {
    "claude": (".claude/settings.json", ".claude/settings.json"),
    "codex": (".codex/hooks.json", ".codex/hooks.json"),
    "gemini": (".gemini/settings.json", ".gemini/settings.json"),
    "cursor": (".cursor/hooks.json", ".cursor/hooks.json"),
    "copilot": (".copilot/hooks/gitvow.json", ".github/hooks/gitvow.json"),
    "factory": (".factory/hooks.json", ".factory/hooks.json"),
}


def _agent_entries(agent: str, cmd_prefix: str) -> dict[str, list[dict[str, Any]]]:
    """Hook entries in each agent's own schema. Every command carries MARKER so install stays idempotent."""
    c = f"{cmd_prefix} hook --agent {agent}"
    if agent == "claude":
        return _hook_entries(cmd_prefix)
    if agent == "codex":

        def e(ev: str, matcher: str | None) -> dict[str, Any]:
            x: dict[str, Any] = {"hooks": [{"type": "command", "command": f"{c} {ev}", "timeout": 30}]}
            if matcher:
                x["matcher"] = matcher
            return x

        return {
            "SessionStart": [e("SessionStart", None)],
            "PreToolUse": [e("PreToolUse", "Bash|apply_patch|mcp__.*")],
            "PostToolUse": [e("PostToolUse", "Bash|apply_patch")],
            "Stop": [e("Stop", None)],
        }
    if agent == "gemini":

        def g(ev: str, matcher: str) -> dict[str, Any]:
            return {
                "matcher": matcher,
                "hooks": [{"name": f"gitvow-{ev}", "type": "command", "command": f"{c} {ev}", "timeout": 30000}],
            }

        return {
            "SessionStart": [g("SessionStart", "*")],
            "BeforeTool": [g("BeforeTool", "run_shell_command|write_file|replace|edit|mcp__.*")],
            "AfterTool": [g("AfterTool", "run_shell_command|write_file|replace|edit")],
            "SessionEnd": [g("SessionEnd", "*")],
        }
    if agent == "copilot":

        def cp(ev: str) -> dict[str, Any]:
            return {"type": "command", "bash": f"{c} {ev}", "timeoutSec": 30}

        return {ev: [cp(ev)] for ev in ("sessionStart", "preToolUse", "postToolUse", "sessionEnd")}
    if agent == "factory":

        def fe(ev: str, matcher: str | None) -> dict[str, Any]:
            x: dict[str, Any] = {"hooks": [{"type": "command", "command": f"{c} {ev}", "timeout": 30}]}
            if matcher:
                x["matcher"] = matcher
            return x

        return {
            "SessionStart": [fe("SessionStart", None)],
            "PreToolUse": [fe("PreToolUse", "Execute|Edit|Create|ApplyPatch|MultiEdit|mcp__.*")],
            "PostToolUse": [fe("PostToolUse", "Execute|Edit|Create|ApplyPatch|MultiEdit")],
            "Stop": [fe("Stop", None)],
        }
    if agent == "cursor":

        def u(ev: str) -> dict[str, Any]:
            return {"command": f"{c} {ev}", "type": "command", "timeout": 30, "failClosed": True}

        return {
            ev: [u(ev)]
            for ev in (
                "sessionStart",
                "preToolUse",
                "beforeShellExecution",
                "beforeMCPExecution",
                "afterFileEdit",
                "afterShellExecution",
                "stop",
            )
        }
    raise ValueError(f"unknown agent {agent}")


def _external(agent: str) -> tuple[str, dict[str, Any]] | None:
    from .adapters import external_info, external_path

    exe = external_path(agent)
    if not exe:
        return None
    return exe, external_info(exe)


def _external_files(info: dict[str, Any]) -> tuple[str, str]:
    inst = info.get("install") or {}
    return str(inst.get("user_file") or f".{info.get('name', 'agent')}/hooks.json"), str(
        inst.get("repo_file") or inst.get("user_file") or f".{info.get('name', 'agent')}/hooks.json"
    )


def merge_external_settings(path: str, info: dict[str, Any], cmd_prefix: str) -> None:
    inst = info.get("install") or {}
    cur: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as fh:
            cur = json.load(fh)
    if inst.get("version_key"):
        cur.setdefault(str(inst["version_key"]), 1)
    hooks = cur.setdefault("hooks", {})
    for ev, entries in (inst.get("entries") or {}).items():
        rendered = json.loads(json.dumps(entries).replace("__GITVOW__", cmd_prefix))
        kept = [x for x in hooks.get(ev, []) if not _is_ours(x)]
        hooks[ev] = kept + rendered
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(cur, fh, indent=2)


def _is_ours(entry: dict[str, Any]) -> bool:
    if MARKER in (entry.get("command") or "") or MARKER in (entry.get("bash") or ""):
        return True
    return any(MARKER in (h.get("command") or "") for h in entry.get("hooks", []))


def merge_agent_settings(path: str, agent: str, cmd_prefix: str) -> None:
    cur: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path) as fh:
            cur = json.load(fh)
    if agent in ("cursor", "copilot"):
        cur.setdefault("version", 1)
    hooks = cur.setdefault("hooks", {})
    for ev, entries in _agent_entries(agent, cmd_prefix).items():
        kept = [x for x in hooks.get(ev, []) if not _is_ours(x)]
        hooks[ev] = kept + entries
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump(cur, fh, indent=2)


def unmerge_agent_settings(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path) as fh:
        cur = json.load(fh)
    hooks = cur.get("hooks", {})
    for ev in list(hooks):
        hooks[ev] = [x for x in hooks[ev] if not _is_ours(x)]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        cur.pop("hooks", None)
    if cur and cur != {"version": 1}:
        with open(path, "w") as fh:
            json.dump(cur, fh, indent=2)
    else:
        os.remove(path)


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


# Agents that re-read their hook configuration while running. Everything else reads it once, at start-up,
# so a session that was already open when gitvow was installed is not gated and says nothing about it.
RELOADS_HOOKS = ("cursor",)


def agent_next_steps(agent: str, home: str, repo: str | None = None) -> list[str]:
    """What gitvow cannot do for you, per agent. Each of these otherwise leaves the gate installed but inert."""
    steps: list[str] = []
    if agent not in RELOADS_HOOKS:
        steps.append(
            f"{agent}: restart it if a session is already open. Hooks are read at start-up, so a session "
            "that began before this install is not gated and will not say so."
        )
    if agent != "codex":
        return steps
    out = [
        *steps,
        "codex: hooks are skipped until trusted. Start codex, run `/hooks`, review the gitvow entries and "
        "trust them; until then codex runs your tools with no gate and says nothing.",
    ]
    gd = os.path.join(repo, ".git") if repo else "<repo>/.git"
    cfg = os.path.join(home, ".codex", "config.toml")
    text = ""
    if os.path.exists(cfg):
        try:
            with open(cfg) as fh:
                text = fh.read()
        except OSError:
            text = ""
    if "hooks = false" in text:
        out.append("codex: hooks are switched off in ~/.codex/config.toml; remove `hooks = false`.")
    if "writable_roots" not in text:
        out.append(
            "codex: its sandbox refuses writes inside .git, so the agent cannot commit at all. Add to "
            f'~/.codex/config.toml:  [sandbox_workspace_write] writable_roots = ["{gd}"]'
        )
    return out


GIT_HOOKS_EXPECTED = ("prepare-commit-msg", "pre-push", "pre-commit", "post-commit")
GIT_HOOK_BODIES = {
    "prepare-commit-msg": GIT_HOOK,
    "pre-push": PRE_PUSH_HOOK,
    "pre-commit": PRE_COMMIT_HOOK,
    "post-commit": POST_COMMIT_HOOK,
}

# How to tell an agent is on this machine: a configuration directory under $HOME, an absolute path, or a
# command on the PATH. Detection only decides what install offers; it never changes what a hook does.
AGENT_PROBES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = {
    "claude": ((".claude",), (), ("claude",)),
    "codex": ((".codex",), (), ("codex",)),
    "gemini": ((".gemini",), (), ("gemini",)),
    "cursor": ((".cursor",), ("/Applications/Cursor.app",), ("cursor-agent", "cursor")),
    "copilot": ((".copilot",), (), ("copilot",)),
    "factory": ((".factory",), (), ("droid",)),
}


def detect_agents(home: str) -> list[str]:
    """Agents that look installed here, built-in first, then any external gitvow-agent-<name> found."""
    found = [
        agent
        for agent, (dirs, paths, bins) in AGENT_PROBES.items()
        if any(os.path.isdir(os.path.join(home, d)) for d in dirs)
        or any(os.path.exists(p) for p in paths)
        or any(shutil.which(b) for b in bins)
    ]
    seen = set(found)
    for d in [os.path.join(home, ".gitvow", "agents"), *os.environ.get("PATH", "").split(os.pathsep)]:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            if n.startswith("gitvow-agent-") and os.access(os.path.join(d, n), os.X_OK):
                name = n[len("gitvow-agent-") :]
                if name not in seen:
                    seen.add(name)
                    found.append(name)
    return found


def hook_commands(path: str) -> list[str]:
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


def installed_agents(home: str, repo: str | None = None) -> list[tuple[str, str, str]]:
    """(agent, scope, path) for every agent settings file that already carries gitvow hooks."""
    out: list[tuple[str, str, str]] = []
    for agent, (user_rel, repo_rel) in AGENT_FILES.items():
        for scope, path in (("user", os.path.join(home, user_rel)), ("repo", os.path.join(repo or "", repo_rel))):
            if scope == "repo" and not repo:
                continue
            if hook_commands(path):
                out.append((agent, scope, path))
    return out


def expected_events(agent: str) -> set[str]:
    """The hook events this version of gitvow installs for an agent."""
    if agent not in AGENT_FILES:
        return set()
    return set(_agent_entries(agent, "gitvow"))


def stale_git_hooks(dirpath: str) -> list[str]:
    """Hooks whose contents are not what this version writes: an install left behind by an older gitvow."""
    out = []
    for name, body in GIT_HOOK_BODIES.items():
        p = os.path.join(dirpath, name)
        try:
            with open(p) as fh:
                if fh.read() != body:
                    out.append(name)
        except OSError:
            continue  # missing hooks are reported separately
    return out


def _write_git_hook(dirpath: str) -> str:
    os.makedirs(dirpath, exist_ok=True)
    for name, body in GIT_HOOK_BODIES.items():
        p = os.path.join(dirpath, name)
        with open(p, "w") as fh:
            fh.write(body)
        os.chmod(p, 0o755)  # noqa: S103  # nosec B103 - git runs hooks as the invoking user; must be executable
    return os.path.join(dirpath, "prepare-commit-msg")


def install_user(home: str, cmd_prefix: str | None = None, agent: str = "claude") -> list[str]:
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
    ext = None if agent in AGENT_FILES else _external(agent)
    settings_rel = AGENT_FILES[agent][0] if agent in AGENT_FILES else _external_files(ext[1])[0]  # type: ignore[index]
    if agent == "claude":
        merge_settings(os.path.join(home, ".claude", "settings.json"), cmd_prefix)
    elif ext:
        merge_external_settings(os.path.join(home, settings_rel), ext[1], cmd_prefix)
    else:
        merge_agent_settings(os.path.join(home, settings_rel), agent, cmd_prefix)
    git(["config", "--global", "core.hooksPath", os.path.join(base, "git-hooks")], home)
    _set_notes_config(home, ["--global"])
    done += [
        f"hooks merged into ~/{settings_rel}",
        "global core.hooksPath → ~/.gitvow/git-hooks",
        f"global notes.displayRef / notes.rewriteRef → {NOTES_GLOB}",
    ]
    return done + agent_next_steps(agent, home)


def uninstall_user(
    home: str, purge_policy: bool = False, purge_ledger: bool = False, agent: str = "claude"
) -> list[str]:
    base = os.path.join(home, ".gitvow")
    done = []
    ext = None if agent in AGENT_FILES else _external(agent)
    user_rel = AGENT_FILES[agent][0] if agent in AGENT_FILES else _external_files(ext[1])[0]  # type: ignore[index]
    if agent == "claude":
        unmerge_settings(os.path.join(home, ".claude", "settings.json"))
    else:
        unmerge_agent_settings(os.path.join(home, user_rel))
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
    done.append(f"hook entries removed from ~/{user_rel}")
    return done


def install_repo(repo: str, cmd_prefix: str = "gitvow", agent: str = "claude") -> list[str]:
    base = os.path.join(repo, ".gitvow")
    os.makedirs(base, exist_ok=True)
    from .policy import DEFAULT_POLICY_PATH

    pol = os.path.join(base, "policy.json")
    if not os.path.exists(pol):
        shutil.copy(DEFAULT_POLICY_PATH, pol)
    _write_git_hook(os.path.join(base, "git-hooks"))
    ext = None if agent in AGENT_FILES else _external(agent)
    repo_rel = AGENT_FILES[agent][1] if agent in AGENT_FILES else _external_files(ext[1])[1]  # type: ignore[index]
    if agent == "claude":
        merge_settings(os.path.join(repo, ".claude", "settings.json"), cmd_prefix)
    elif ext:
        merge_external_settings(os.path.join(repo, repo_rel), ext[1], cmd_prefix)
    else:
        merge_agent_settings(os.path.join(repo, repo_rel), agent, cmd_prefix)
    git(["config", "core.hooksPath", ".gitvow/git-hooks"], repo)
    _set_notes_config(repo, [])
    return [
        f"policy → {pol}",
        f"hooks merged into {repo_rel}",
        "core.hooksPath → .gitvow/git-hooks",
        f"notes.displayRef / notes.rewriteRef → {NOTES_GLOB}",
        "commit .gitvow/ and .claude/settings.json to share; teammates run: git config core.hooksPath .gitvow/git-hooks",
        *agent_next_steps(agent, os.path.expanduser("~"), repo),
    ]


def uninstall_repo(
    repo: str, purge_notes: bool = False, purge_snapshots: bool = False, agent: str = "claude"
) -> list[str]:
    done = []
    ext = None if agent in AGENT_FILES else _external(agent)
    repo_rel = AGENT_FILES[agent][1] if agent in AGENT_FILES else _external_files(ext[1])[1]  # type: ignore[index]
    if agent == "claude":
        unmerge_settings(os.path.join(repo, ".claude", "settings.json"))
    else:
        unmerge_agent_settings(os.path.join(repo, repo_rel))
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
    if purge_snapshots:
        from . import snapshots as _snap

        done.append(f"{_snap.purge_all(repo)} snapshot ref(s) deleted")
    done.append(".gitvow removed; commit trailers already in history remain")
    return done
