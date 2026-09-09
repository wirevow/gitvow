"""Self-check: drives every hook in a throwaway repository and reports pass/fail. Never touches a real repository."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .hooks import post_tool_use, pre_tool_use, session_start, stop
from .install import _write_git_hook


def run_agent(agent: str) -> int:
    """Drive the adapter for another agent with that agent's payload shapes: a block and a snapshot."""
    import json as _json

    from .adapters import external_normalize, external_path, external_respond, normalize, respond
    from .hooks import HANDLERS

    exe = external_path(agent)

    home = tempfile.mkdtemp(prefix="gitvow-home-")
    repo = tempfile.mkdtemp(prefix="gitvow-repo-")
    results: list[tuple[bool, str]] = []
    try:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=False)
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        samples: dict[str, list[tuple[str, dict[str, Any]]]] = {
            "codex": [
                ("SessionStart", {"session_id": "st", "cwd": repo}),
                (
                    "PreToolUse",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "Bash",
                        "tool_input": {"command": "git push --force"},
                    },
                ),
                (
                    "PostToolUse",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "apply_patch",
                        "tool_input": {"command": "*** Begin Patch\n*** Update File: a.txt\n+b\n*** End Patch\n"},
                    },
                ),
            ],
            "gemini": [
                ("SessionStart", {"session_id": "st", "cwd": repo}),
                (
                    "BeforeTool",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "run_shell_command",
                        "tool_input": {"command": "git push --force"},
                    },
                ),
                (
                    "AfterTool",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "write_file",
                        "tool_input": {"file_path": os.path.join(repo, "a.txt"), "content": "b"},
                    },
                ),
            ],
            "copilot": [
                ("sessionStart", {"sessionId": "st", "cwd": repo}),
                (
                    "preToolUse",
                    {"sessionId": "st", "cwd": repo, "toolName": "bash", "toolArgs": {"command": "git push --force"}},
                ),
                (
                    "postToolUse",
                    {
                        "sessionId": "st",
                        "cwd": repo,
                        "toolName": "edit",
                        "toolArgs": {"path": os.path.join(repo, "a.txt"), "old_str": "a", "new_str": "b"},
                    },
                ),
            ],
            "factory": [
                ("SessionStart", {"session_id": "st", "cwd": repo}),
                (
                    "PreToolUse",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "Execute",
                        "tool_input": {"command": "git push --force"},
                    },
                ),
                (
                    "PostToolUse",
                    {
                        "session_id": "st",
                        "cwd": repo,
                        "tool_name": "Edit",
                        "tool_input": {"file_path": os.path.join(repo, "a.txt"), "old_string": "a", "new_string": "b"},
                    },
                ),
            ],
            "cursor": [
                ("sessionStart", {"conversation_id": "st", "workspace_roots": [repo]}),
                (
                    "beforeShellExecution",
                    {"conversation_id": "st", "workspace_roots": [repo], "command": "git push --force"},
                ),
                (
                    "afterFileEdit",
                    {
                        "conversation_id": "st",
                        "workspace_roots": [repo],
                        "file_path": os.path.join(repo, "a.txt"),
                        "edits": [{"old_string": "a", "new_string": "b"}],
                    },
                ),
            ],
        }
        if exe:
            # external adapters are exercised with a generic shape: whatever they cannot map is a no-op
            samples[agent] = [
                ("start", {"sid": "st", "dir": repo}),
                ("beforeTool", {"sid": "st", "dir": repo, "tool": "sh", "args": {"cmd": "git push --force"}}),
                (
                    "afterTool",
                    {
                        "sid": "st",
                        "dir": repo,
                        "tool": "edit",
                        "args": {"path": os.path.join(repo, "a.txt"), "before": "a", "after": "b"},
                    },
                ),
            ]
        for ev, payload in samples[agent]:
            worst, msgs = 0, []
            for gv_event, p in external_normalize(exe, ev, payload) if exe else normalize(agent, ev, payload):
                code, msg = HANDLERS[gv_event](p, home)
                worst = max(worst, code)
                if msg:
                    msgs.append(msg)
            exit_code, out, _err = (
                external_respond(exe, worst, "\n".join(msgs)) if exe else respond(agent, worst, "\n".join(msgs))
            )
            if ev in ("PreToolUse", "BeforeTool", "beforeShellExecution", "preToolUse", "beforeTool"):
                if exe:
                    blocked = exit_code == 2 or ("deny" in out)
                elif agent == "cursor":
                    blocked = _json.loads(out).get("permission") == "deny"
                elif agent == "copilot":
                    blocked = _json.loads(out).get("permissionDecision") == "deny"
                else:
                    blocked = exit_code == 2
                results.append((blocked, f"{agent}: force push denied in the agent's own form"))
        refs = subprocess.run(
            ["git", "for-each-ref", "refs/gitvow/snapshots/"], cwd=repo, capture_output=True, text=True
        ).stdout
        results.append((refs.strip() != "", f"{agent}: snapshot taken after the agent's edit"))
        results.append(
            (os.path.exists(os.path.join(repo, ".git", "gitvow-session.json")), f"{agent}: session recorded")
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)
    for ok, name in results:
        print(("  ok   " if ok else "  FAIL ") + name)
    failed = sum(1 for ok, _ in results if not ok)
    print(
        f"\nselftest --agent {agent}: {len(results) - failed} passed, {failed} failed (payload shapes from vendor docs; not a real session)"
    )
    return 1 if failed else 0


def run() -> int:
    home = tempfile.mkdtemp(prefix="gitvow-home-")
    repo = tempfile.mkdtemp(prefix="gitvow-repo-")
    results: list[tuple[bool, str]] = []

    def g(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True).stdout.strip()

    try:
        g("init", "-q")
        g("config", "user.email", "selftest@local")
        g("config", "user.name", "selftest")
        with open(os.path.join(repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        g("add", "a.txt")
        g("commit", "-qm", "init")
        base = {"session_id": "selftest-session", "transcript_path": "", "cwd": repo}
        session_start(base, home)
        results.append((os.path.exists(os.path.join(repo, ".git", "gitvow-session.json")), "session recorded in .git"))
        results.append(
            (
                pre_tool_use(
                    {**base, "tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}, home
                )[0]
                == 2,
                "deny: force push blocked",
            )
        )
        results.append(
            (
                pre_tool_use({**base, "tool_name": "Bash", "tool_input": {"command": "git push origin feat"}}, home)[0]
                == 2,
                "confirm: git push requires asking",
            )
        )
        results.append(
            (
                pre_tool_use({**base, "tool_name": "Bash", "tool_input": {"command": "ls -la"}}, home)[0] == 0,
                "allow: harmless command",
            )
        )
        results.append(
            (
                pre_tool_use({**base, "tool_name": "Edit", "tool_input": {"file_path": "x/authz_rules.py"}}, home)[0]
                == 2,
                "confirm: gate-bearing file edit",
            )
        )
        results.append(
            (
                pre_tool_use({**base, "tool_name": "mcp__x__delete_thing", "tool_input": {}}, home)[0] == 2,
                "deny: destructive MCP tool",
            )
        )
        prov = (
            f'{sys.executable} -c "import sys,json;q=json.load(sys.stdin);'
            "print(json.dumps({'answer':'yes' if q['question']=='route_gate' else 'no',"
            "'evidence':['route not covered by authorization (selftest provider)']}))\""
        )
        os.makedirs(os.path.join(repo, ".gitvow"), exist_ok=True)
        with open(os.path.join(repo, ".gitvow", "policy.json"), "w") as fh:
            json.dump({"providers": [{"name": "selftest", "command": prov}]}, fh)
        code, msg = pre_tool_use(
            {
                **base,
                "tool_name": "Edit",
                "tool_input": {"file_path": "api.py", "old_string": "", "new_string": '"/v1/new"'},
            },
            home,
        )
        results.append((code == 2 and "route not covered" in msg, "confirm: provider says new route is unauthorised"))
        os.remove(os.path.join(repo, ".gitvow", "policy.json"))
        with open(os.path.join(repo, "a.txt"), "a") as fh:
            fh.write("b\n")
        post_tool_use({**base, "tool_name": "Edit", "tool_input": {"file_path": "a.txt"}}, home)
        snaps = g("for-each-ref", "--format=%(refname)", "refs/gitvow/snapshots/").splitlines()
        results.append(
            (len(snaps) == 1 and snaps[0].endswith("/selftest-session/1"), "snapshot taken after the agent edit")
        )
        results.append(
            (
                g("show", f"{snaps[0]}:a.txt") == "a\nb" if snaps else False,
                "snapshot holds the agent's version of the file",
            )
        )
        with open(os.path.join(repo, "a.txt"), "a") as fh:
            fh.write("human\n")
        pre_tool_use({**base, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, home)
        hooks_dir = os.path.join(repo, ".gitvow", "git-hooks")
        _write_git_hook(hooks_dir)
        g("config", "core.hooksPath", ".gitvow/git-hooks")
        g("config", "notes.rewriteRef", "refs/notes/gitvow/*")
        g("commit", "-qam", "selftest commit")
        results.append(("Gitvow-Session:" in g("log", "-1", "--format=%B"), "commit trailer added"))
        post_tool_use({**base, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, home)
        note = g("notes", "--ref=gitvow/selftest-session", "show", "HEAD")
        results.append((note.startswith("gitvow-session"), "session note attached on the session's own ref"))
        att = json.loads(note.split("\n", 1)[1])["attribution"] if note else {}
        results.append(
            (att.get("lines_changed_by_human_after_agent") == 1 and att.get("agent_share") == 0.5, "line attribution")
        )
        g("commit", "-q", "--amend", "-m", "amended")
        results.append(
            (
                g("notes", "--ref=gitvow/selftest-session", "show", "HEAD").startswith("gitvow-session"),
                "note follows amend",
            )
        )
        stop(base, home)
        results.append(
            (os.path.exists(os.path.join(home, ".gitvow", "ledger", "selftest-session.json")), "ledger written")
        )
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)
    for ok, name in results:
        print(("  ok   " if ok else "  FAIL ") + name)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\nselftest: {len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0
