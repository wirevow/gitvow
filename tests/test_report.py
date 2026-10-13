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


def test_report_carries_intent_cites_and_standing(repo, home, monkeypatch, capsys):
    """0.41: decision lines cite the observations the finding rested on and what the record said before; the
    description block carries the intent and the cites as non-trailer lines."""
    base = git(repo, "rev-parse", "HEAD")
    finding = "edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)"
    (repo / "api").mkdir()
    (repo / "api" / "reports.py").write_text("v1\n")
    git(repo, "add", "api")
    git(
        repo,
        "commit",
        "-qm",
        f"first\n\nGitvow-Session: s1\nGitvow-Step: 1\nGitvow-Intent: fix the reports route by priya\nGitvow-Declined: {finding} by priya: not until the 5xx is understood\n",
    )
    note = {
        "schema": 10,
        "step": 1,
        "intent": {"text": "fix the reports route", "by": "priya"},
        "decisions": [
            {
                "finding": finding,
                "answer": "declined",
                "by": "priya",
                "observations": ["obs_" + "c" * 32],
                "evidence": ["200 requests in 7d, 5xx 3.0%"],
            }
        ],
    }
    git(repo, "notes", "--ref=gitvow/s1", "add", "-f", "-m", "gitvow-session\n" + json.dumps(note), "HEAD")
    (repo / "api" / "reports.py").write_text("v2\n")
    git(
        repo,
        "commit",
        "-qam",
        f"second\n\nGitvow-Session: s2\nGitvow-Step: 1\nGitvow-Intent: fix the reports route by priya\nGitvow-Accepted: {finding} by priya: fixed the handler\n",
    )
    note2 = {
        "schema": 10,
        "step": 1,
        "intent": {"text": "fix the reports route", "by": "priya"},
        "decisions": [
            {
                "finding": finding,
                "answer": "accepted",
                "by": "priya",
                "observations": ["obs_" + "c" * 32, "obs_" + "d" * 32],
            }
        ],
    }
    git(repo, "notes", "--ref=gitvow/s2", "add", "-f", "-m", "gitvow-session\n" + json.dumps(note2), "HEAD")
    r = build(str(repo), base, "HEAD")
    first, second = r["commits"]
    assert (
        first["decisions"][0]["observations"] == ["obs_" + "c" * 32]
        and first["decisions"][0]["standing"] == "no earlier decision."
    )
    assert second["decisions"][0]["observations"] == ["obs_" + "c" * 32, "obs_" + "d" * 32]
    assert (
        "declined" in second["decisions"][0]["standing"]
    )  # the first commit's decision is the record before the second
    md = render_markdown(r)
    assert (
        "**Intent:** fix the reports route" in md
        and "cites obs_cccc" in md
        and "record before this: no earlier decision." in md
    )
    assert "record before this: " in md.split("### ")[2]
    from gitvow.report import decisions_summary

    block = decisions_summary(r)
    assert block.splitlines()[1] == "Gitvow-Intent: fix the reports route"
    assert (
        f"Gitvow-Cites: {finding} <- obs_{'c' * 32}" in block
        and f"Gitvow-Cites: {finding} <- obs_{'c' * 32}, obs_{'d' * 32}" in block
    )
    # the trailer grammar never reads a cites line as a decision
    from gitvow.decisions import parse_trailers

    assert (
        all(t["answer"] in ("accepted", "declined") for t in parse_trailers(block)) and len(parse_trailers(block)) == 2
    )
    monkeypatch.chdir(repo)
    assert cli.main(["report", "--base", base, "--decisions-summary"]) == 0
    assert "Gitvow-Cites" in capsys.readouterr().out
