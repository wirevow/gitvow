"""Self-check: drives every hook in a throwaway repository and reports pass/fail. Never touches a real repository."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from .hooks import post_tool_use, pre_tool_use, session_start, stop
from .install import _write_git_hook


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
