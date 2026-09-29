"""0.34: the record must not depend on git hooks. Gemini CLI disables them for every command it runs."""

import json
import subprocess

from gitvow import decisions as dec
from gitvow.hooks import (
    _hooks_why,
    hooks_disabled_by_env,
    post_tool_use,
    pre_tool_use,
    session_start,
    user_prompt_submit,
)
from gitvow.install import _write_git_hook
from tests.conftest import git

GEMINI_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_COUNT": "3",
    "GIT_CONFIG_KEY_0": "credential.helper",
    "GIT_CONFIG_VALUE_0": "",
    "GIT_CONFIG_KEY_1": "core.hooksPath",
    "GIT_CONFIG_VALUE_1": "",
    "GIT_CONFIG_KEY_2": "core.pager",
    "GIT_CONFIG_VALUE_2": "cat",
}


def test_detects_the_environment_gemini_runs_commands_in():
    assert hooks_disabled_by_env(GEMINI_ENV) is True
    assert hooks_disabled_by_env({}) is False
    assert (
        hooks_disabled_by_env({"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pager", "GIT_CONFIG_VALUE_0": "cat"})
        is False
    )
    assert hooks_disabled_by_env({"GIT_CONFIG_COUNT": "x"}) is False


def test_a_commit_the_hooks_missed_gets_its_trailers_note_and_bookkeeping(repo, home, payload, transcript, monkeypatch):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    session_start(payload("SessionStart"), str(home))
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": "Let ops export orders as CSV."}, str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": ".github/workflows/ci.yml"}, transcript), str(home))
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -am x"}, transcript), str(home))
    assert code == 2  # the card
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -am x"}, transcript), str(home))
    assert code == 0  # acknowledged; the commit may go
    # the agent's shell runs git with hooks off, as Gemini does: no trailer, no post-commit bookkeeping
    (repo / "a.txt").write_text("changed\n")
    subprocess.run(["git", "-c", "core.hooksPath=/nonexistent", "commit", "-qam", "gm ci"], cwd=repo, check=True)
    before = git(repo, "rev-parse", "HEAD")
    assert "Gitvow-" not in git(repo, "log", "-1", "--format=%B")
    for k, v in GEMINI_ENV.items():
        monkeypatch.setenv(k, v)
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -am x"}, transcript), str(home))
    assert (
        code == 0
        and "session note attached" in msg
        and "trailers written by gitvow because the git hooks did not run" in msg
    )
    assert "core.hooksPath overridden in its environment" in msg
    after = git(repo, "rev-parse", "HEAD")
    body = git(repo, "log", "-1", "--format=%B")
    assert after != before and body.startswith("gm ci\n")
    assert "Gitvow-Session: sess-1" in body and "Gitvow-Step: 1" in body
    assert "Gitvow-Intent: Let ops export orders as CSV. by t source=prompt" in body
    assert "Gitvow-Open: edit .github/workflows/ci.yml" in body
    assert git(repo, "log", "-1", "--format=%an", after) == "t" and git(repo, "rev-list", "--count", "HEAD") == "2"
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", after).split("\n", 1)[1])
    assert note["decisions"][0]["answer"] == "open" and note["intent"]["text"].startswith("Let ops")
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"] == [] and "last_commit" not in st  # moved by the repair, consumed by the note
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "trailers_repaired"' in log and before[:12] in log
    # a commit that already carries the trailer is never amended (hooks back on: the environment is ordinary again)
    for k in GEMINI_ENV:
        monkeypatch.delenv(k, raising=False)
    (repo / "a.txt").write_text("again\n")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -am y"}, transcript), str(home))
    git(repo, "commit", "-qam", "with hooks")
    h2 = git(repo, "rev-parse", "HEAD")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -am y"}, transcript), str(home))
    assert git(repo, "rev-parse", "HEAD") == h2
    assert (repo / ".git" / "gitvow-hooks.log").read_text().count("trailers_repaired") == 1


def test_a_persons_commit_and_a_late_commit_are_left_alone(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    # no pending commit: a person committed from a terminal; nothing is amended
    (repo / "a.txt").write_text("human\n")
    subprocess.run(["git", "-c", "core.hooksPath=/nonexistent", "commit", "-qam", "by hand"], cwd=repo, check=True)
    h = git(repo, "rev-parse", "HEAD")
    assert post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -am x"}, transcript), str(home)) == (
        0,
        "",
    )
    assert git(repo, "rev-parse", "HEAD") == h
    assert _hooks_why().startswith("core.hooksPath does not reach")


def test_notes_are_pushed_after_the_agents_push_when_hooks_are_off(repo, home, payload, tmp_path, monkeypatch):
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "notes", "--ref=gitvow/s1", "add", "-m", "gitvow-session\n{}", "HEAD")
    session_start(payload("SessionStart"), str(home))
    subprocess.run(
        ["git", "-c", "core.hooksPath=/nonexistent", "push", "-q", "origin", "HEAD:refs/heads/main"],
        cwd=repo,
        check=True,
    )
    assert git(bare, "for-each-ref", "refs/notes/") == ""
    # hooks on: gitvow trusts the pre-push hook and does nothing here
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git push origin main"}), str(home))
    assert git(bare, "for-each-ref", "refs/notes/") == ""
    for k, v in GEMINI_ENV.items():
        monkeypatch.setenv(k, v)
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git push origin main"}), str(home))
    assert git(bare, "rev-parse", "refs/notes/gitvow/s1") == git(repo, "rev-parse", "refs/notes/gitvow/s1")
    assert '"kind": "notes_pushed_by_hook"' in (repo / ".git" / "gitvow-hooks.log").read_text()


def test_identity_falls_back_to_the_gitconfig_file_gemini_hides(repo, home, monkeypatch):
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "--unset", "user.name")
    (home / ".gitconfig").write_text("[user]\n\temail = priya@acme.example\n\tname = Priya Nair\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    assert dec.identity(str(repo))[0] == "priya"
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    assert dec.identity(str(repo))[0] == "priya"  # the ordinary path still reads it
