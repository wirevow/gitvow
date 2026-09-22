"""0.28.1: the pending-commit window is policy, and a commit that lands past it is said out loud."""

import json
import time

from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from tests.conftest import git


def _hooked(repo):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")


def test_window_comes_from_policy_and_old_states_keep_five_minutes(repo, home, payload):
    _hooked(repo)
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    p = repo / ".git" / "gitvow-session.json"
    st = json.loads(p.read_text())
    assert st["pending_ttl"] == 1800
    # ten minutes ago is inside the default window: the trailer is written
    st["pending_commit"] = time.time() - 600
    p.write_text(json.dumps(st))
    (repo / "a.txt").write_text("1\n")
    git(repo, "commit", "-qam", "queued ten minutes")
    assert "Gitvow-Session: sess-1" in git(repo, "log", "-1", "--format=%B")
    # a state written by an older gitvow has no window and keeps the original five minutes
    st = json.loads(p.read_text())
    st["pending_commit"] = time.time() - 600
    st.pop("pending_ttl", None)
    p.write_text(json.dumps(st))
    (repo / "a.txt").write_text("2\n")
    git(repo, "commit", "-qam", "old state, ten minutes")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")


def test_a_commit_past_the_window_is_reported_and_logged(repo, home, payload, monkeypatch, tmp_path):
    _hooked(repo)
    pol = tmp_path / "policy.json"
    from gitvow.policy import DEFAULT_POLICY_PATH

    with open(DEFAULT_POLICY_PATH) as fh:
        d = json.load(fh)
    d["decisions"]["commit_window_seconds"] = 1
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert json.loads((repo / ".git" / "gitvow-session.json").read_text())["pending_ttl"] == 1
    time.sleep(1.2)
    (repo / "a.txt").write_text("late\n")
    git(repo, "commit", "-qam", "late commit")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert code == 0 and "past the 1s window" in msg and "carries no session trailer" in msg
    assert '"kind": "commit_without_trailer"' in (repo / ".git" / "gitvow-hooks.log").read_text()
    assert pol is not None
