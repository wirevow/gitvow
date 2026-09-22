"""0.28.4: a command that both commits and runs a session-answered confirm still gets its commit bookkeeping.

Found on the founder's machine: after a push to main had been accepted for the session, `git commit … && git push`
in one command was allowed by the session answer and returned before the commit flag was set, so the commit carried
no session trailer. Three of four repositories lost the trailer on the same evening the same way.
"""

import json

from gitvow import decisions as dec
from gitvow.hooks import pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.policy import load_policy
from tests.conftest import git


def test_session_answered_push_in_the_same_command_as_a_commit_keeps_the_trailer(repo, home, payload):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    git(repo, "remote", "add", "origin", "git@example.com:acme/ledger.git")
    session_start(payload("SessionStart"), str(home))
    cmd = {"command": 'git commit -qm "x" && git push origin main'}
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", cmd), str(home))
    assert code == 2 and "pushing to a protected branch" in msg
    dec.decide(str(repo), "1", "accept", load_policy(str(repo), str(home)), scope="session", reason="ok", rules=[])
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", cmd), str(home))
    assert code == 0
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st.get("pending_commit") and st["steps"] == 1
    (repo / "a.txt").write_text("y\n")
    git(repo, "commit", "-qam", "after the answer")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: sess-1" in body and "Gitvow-Accepted: run git (pushing to a protected branch)" in body
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "allowed_by_session_answer"' in log
