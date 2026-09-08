import json
import os

from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git


def test_session_start_records_state(repo, home, payload):
    assert session_start(payload("SessionStart"), str(home)) == (0, "")
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["session_id"] == "sess-1" and st["steps"] == 0


def test_pre_tool_use_blocks_and_logs(repo, home, payload):
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push --force"}), str(home))
    assert code == 2 and "BLOCKED" in msg
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "blocked"' in log and "force push" in log


def test_pre_tool_use_fails_closed_without_policy(repo, home, payload, monkeypatch):
    import gitvow.policy as pm

    monkeypatch.setattr(pm, "DEFAULT_POLICY_PATH", "/nonexistent/policy.json")
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "ls"}), str(home))
    assert code == 2 and "could not be loaded" in msg


def test_commit_flow_trailer_note_and_attribution(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 0
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("changed\n")
    git(repo, "commit", "-qam", "feature")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: sess-1" in body and "Gitvow-Step: 1" in body
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "session note attached" in msg
    note = git(repo, "notes", "--ref=sessions", "show", "HEAD")
    assert note.startswith("gitvow-session")
    data = json.loads(note.split("\n", 1)[1])
    assert data["step"] == 1 and data["tools_used"] == ["Bash", "Edit"]
    assert "[github-token]" in data["last_stated_plan"] and "[email:" in data["last_stated_plan"]
    assert "ghp_" not in note and "ops@example.com" not in note
    assert data["attribution"] == {"files_in_commit": 1, "touched_by_agent": 1}


def test_human_commit_has_no_trailer(repo, home):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("h\n")
    git(repo, "commit", "-qam", "human")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")


def test_git_hook_chains_to_repo_hook(repo, home):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    local = repo / ".git" / "hooks" / "prepare-commit-msg"
    local.write_text('#!/bin/sh\necho LOCAL >> "$1"\n')
    os.chmod(local, 0o700)
    (repo / ".git" / "gitvow-session.json").write_text(json.dumps({"session_id": "s", "steps": 2}))
    (repo / "a.txt").write_text("c\n")
    git(repo, "commit", "-qam", "chain")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: s" in body and "LOCAL" in body


def test_stop_writes_ledger_outside_repo(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    stop(payload("Stop", transcript=transcript), str(home))
    led = home / ".gitvow" / "ledger" / "sess-1.json"
    rec = json.loads((led).read_text())
    assert rec["repo"] == str(repo) and rec["tool_calls"][0]["arg"] == "grep -r password=[redacted] config/"
    assert not (repo / ".gitvow").exists()


def test_post_tool_use_ignores_non_commits(repo, home, payload):
    assert post_tool_use(payload("PostToolUse", "Bash", {"command": "ls"}), str(home)) == (0, "")
