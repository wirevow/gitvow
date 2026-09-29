"""0.32.1: three defects from the first day the record was graded. push-notes verifies; the hook speaks; intent nudges."""

import json
import subprocess
import time

import pytest

from gitvow import cli
from gitvow import intent as im
from gitvow.cli import _notes_not_on_remote
from gitvow.hooks import session_start, user_prompt_submit
from gitvow.install import PRE_PUSH_HOOK, _write_git_hook
from gitvow.policy import DEFAULT_POLICY_PATH, PolicyError, load_policy
from tests.conftest import git


def _bare_remote(tmp_path, repo):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    return bare


def test_push_notes_pushes_and_then_checks_the_remote(repo, home, tmp_path, monkeypatch, capsys):
    bare = _bare_remote(tmp_path, repo)
    git(repo, "notes", "--ref=gitvow/s1", "add", "-m", "gitvow-session\n{}", "HEAD")
    monkeypatch.chdir(repo)
    assert _notes_not_on_remote(str(repo), "origin") == [
        ("refs/notes/gitvow/s1", git(repo, "rev-parse", "refs/notes/gitvow/s1"), None)
    ]
    assert cli.main(["push-notes"]) == 0
    assert "refs/notes/gitvow/s1" in capsys.readouterr().out
    assert _notes_not_on_remote(str(repo), "origin") == []
    assert git(bare, "rev-parse", "refs/notes/gitvow/s1") == git(repo, "rev-parse", "refs/notes/gitvow/s1")
    # a ref the remote holds at a different sha is named after the push, and the command fails loudly
    git(repo, "notes", "--ref=gitvow/s1", "add", "-f", "-m", 'gitvow-session\n{"x": 1}', "HEAD")
    local = git(repo, "rev-parse", "refs/notes/gitvow/s1")
    monkeypatch.setattr(subprocess, "run", _pretend_push_did_nothing(subprocess.run))
    assert cli.main(["push-notes"]) == 1
    err = capsys.readouterr().err
    assert f"refs/notes/gitvow/s1: local {local[:12]}, remote" in err and "1 notes ref not on origin" in err


def _pretend_push_did_nothing(real):
    def run(args, *a, **kw):
        if args[:2] == ["git", "push"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="Everything up-to-date")
        return real(args, *a, **kw)

    return run


def test_pre_push_hook_reports_a_failed_notes_push_and_lets_the_branch_through(repo, home, tmp_path):
    bare = _bare_remote(tmp_path, repo)
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    git(repo, "notes", "--ref=gitvow/s1", "add", "-m", "gitvow-session\n{}", "HEAD")
    (repo / "a.txt").write_text("2\n")
    git(repo, "commit", "-qam", "two")
    # a remote that refuses notes refs: the branch still lands and the hook says what failed
    hook = bare / "hooks" / "pre-receive"
    hook.write_text(
        '#!/bin/sh\nwhile read o n r; do case "$r" in refs/notes/*) echo "notes refused" >&2; exit 1;; esac; done\nexit 0\n'
    )
    hook.chmod(0o755)
    r = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "gitvow: pushing refs/notes/gitvow/* to origin failed" in r.stderr and "notes refused" in r.stderr
    assert "run: gitvow push-notes origin" in r.stderr
    assert git(bare, "rev-parse", "refs/heads/main") == git(repo, "rev-parse", "HEAD")
    assert (repo / ".git" / "gitvow-notes-push.err").exists()
    # a remote that accepts them: silence, and the notes arrive with the branch
    hook.unlink()
    (repo / "a.txt").write_text("3\n")
    git(repo, "commit", "-qam", "three")
    r = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0 and "gitvow:" not in r.stderr
    assert git(bare, "rev-parse", "refs/notes/gitvow/s1") == git(repo, "rev-parse", "refs/notes/gitvow/s1")
    assert "2>/dev/null || true" not in PRE_PUSH_HOOK


def test_a_task_shaped_message_long_after_the_intent_nudges_the_agent_once(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    rc, out = user_prompt_submit({**payload("UserPromptSubmit"), "prompt": "Let ops export orders as CSV."}, str(home))
    assert rc == 0 and out == ""
    # minutes later, another task-shaped message: no nudge yet, the window is an hour by default
    rc, out = user_prompt_submit(
        {**payload("UserPromptSubmit"), "prompt": "now build the outcomes join for it"}, str(home)
    )
    assert out == ""
    # two hours later: the agent is told, the person is not asked, the record is unchanged
    st_path = repo / ".git" / "gitvow-session.json"
    st = json.loads(st_path.read_text())
    st["intent"]["set_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 2 * 3600))
    st_path.write_text(json.dumps(st))
    rc, out = user_prompt_submit(
        {**payload("UserPromptSubmit"), "prompt": "now build the outcomes join for it"}, str(home)
    )
    assert rc == 0 and out.startswith(
        'gitvow: this session\'s intent was recorded 2h ago as "Let ops export orders as CSV."'
    )
    assert 'gitvow intent "<their words>"' in out and "If it is the same task, do nothing." in out
    assert im.current(str(repo))["text"] == "Let ops export orders as CSV."
    # a nudge, not a nag: a one-word message two hours later says nothing
    rc, out = user_prompt_submit({**payload("UserPromptSubmit"), "prompt": "go"}, str(home))
    assert out == ""
    # off by policy
    with open(DEFAULT_POLICY_PATH) as fh:
        d = json.load(fh)
    d["intent"]["restate_after_minutes"] = 0
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    rc, out = user_prompt_submit(
        {**payload("UserPromptSubmit"), "prompt": "now build the outcomes join for it"}, str(home)
    )
    assert out == ""
    d["intent"]["restate_after_minutes"] = -5
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    with pytest.raises(PolicyError, match="restate_after_minutes"):
        load_policy(str(repo), str(home))
    assert im.settings({})["restate_after_minutes"] == 60
    assert im.stale_nudge({"text": "x", "set_at": "not a time"}, "some task shaped text", im.settings({})) == ""
