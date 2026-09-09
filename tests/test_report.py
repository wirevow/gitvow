import json
import time

from gitvow import cli
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.report import build, render_markdown, said_vs_did
from tests.conftest import git


def test_said_vs_did_matches_path_basename_or_stem():
    r = said_vs_did(
        "Fix the cache key in QueryCacheHelper.java and touch calc", ["a/QueryCacheHelper.java", "calc.py", "x/util.go"]
    )
    assert r == {"mentioned": ["a/QueryCacheHelper.java", "calc.py"], "unmentioned": ["x/util.go"]}
    assert said_vs_did("", ["a.py"]) == {"mentioned": [], "unmentioned": ["a.py"]}


def _agent_commit(repo, home, payload, transcript, sid, text):
    p = payload("SessionStart", transcript=transcript)
    p["session_id"] = sid
    session_start(p, str(home))
    (repo / "a.txt").write_text(text)
    post_tool_use({**p, "tool_name": "Edit", "tool_input": {"file_path": "a.txt"}}, str(home))
    pre_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))
    git(repo, "commit", "-qam", f"agent {sid}")
    post_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))


def test_report_pairs_trailers_with_notes_and_flags_missing(repo, home, payload, transcript, monkeypatch, capsys):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    base = git(repo, "rev-parse", "HEAD")
    _agent_commit(repo, home, payload, transcript, "s-aaa", "one\n")
    (repo / "h.txt").write_text("human\n")
    git(repo, "add", "h.txt")
    git(repo, "commit", "-qm", "human commit")
    _agent_commit(repo, home, payload, transcript, "s-bbb", "two\n")
    git(repo, "update-ref", "-d", "refs/notes/gitvow/s-bbb")  # simulate notes never pushed
    r = build(str(repo), base, "HEAD")
    assert r["agent_commits"] == 2 and r["human_commits"] == 1 and len(r["missing_notes"]) == 1
    kinds = [c["kind"] for c in r["commits"]]
    assert kinds == ["agent", "human", "agent"]
    first = r["commits"][0]
    assert first["note_found"] and first["attribution"]["agent_share"] == 1.0 and first["step"] == 1
    assert "[github-token]" in first["plan"]  # redacted plan travels into the report
    assert first["said_vs_did"]["unmentioned"] == ["a.txt"]
    md = render_markdown(r)
    assert "2 agent commits, 1 human commit" in md and "Missing session notes" in md
    assert "made by a person" in md and "not mentioned in plan" in md and "ghp_" not in md
    monkeypatch.chdir(repo)
    assert cli.main(["report", "--base", base]) == 0
    assert cli.main(["report", "--base", base, "--require-notes"]) == 1
    assert "missing session notes" in capsys.readouterr().err
    assert cli.main(["report", "--base", base, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agent_commits"] == 2
    assert cli.main(["report", "--base", "nonexistent-rev"]) == 1


def test_pre_push_hook_pushes_notes_with_branch(repo, home, tmp_path):
    remote = tmp_path / "remote.git"
    git(remote.parent, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    assert (repo / ".gitvow" / "git-hooks" / "pre-push").exists()
    git(repo, "notes", "--ref=gitvow/s1", "add", "-m", "gitvow-session\n{}", "HEAD")
    branch = git(repo, "branch", "--show-current")
    git(repo, "push", "-q", "origin", branch)
    assert git(remote, "for-each-ref", "--format=%(refname)", "refs/notes/gitvow/") == "refs/notes/gitvow/s1"
    # push-notes by hand, and idempotent second push
    (repo / "a.txt").write_text("n\n")
    git(repo, "commit", "-qam", "n")
    git(repo, "notes", "--ref=gitvow/s2", "add", "-m", "gitvow-session\n{}", "HEAD")
    import os

    cwd = os.getcwd()
    os.chdir(repo)
    try:
        assert cli.main(["push-notes"]) == 0
    finally:
        os.chdir(cwd)
    assert "refs/notes/gitvow/s2" in git(remote, "for-each-ref", "--format=%(refname)", "refs/notes/gitvow/")
    assert time.time() > 0
