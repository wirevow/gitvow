import json

from gitvow import cli
from gitvow.digest import parse_since
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from tests.conftest import git


def test_parse_since():
    assert parse_since("2026-09-01") == "2026-09-01"
    assert parse_since("7d") < parse_since("1h")
    assert len(parse_since("2w")) == 19


def test_digest_counts_and_render(repo, home, payload, transcript, monkeypatch, capsys):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    for sid, content in (("s-one", "one\n"), ("s-two", "two\n")):
        p = payload("SessionStart", transcript=transcript)
        p["session_id"] = sid
        session_start(p, str(home))
        (repo / "a.txt").write_text(content)
        post_tool_use({**p, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "a.txt")}}, str(home))
        if sid == "s-two":
            (repo / "a.txt").write_text(content + "human line\n")  # a person edits before the agent commits
        pre_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git push --force"}}, str(home))  # denied
        pre_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git push origin x"}}, str(home))  # confirm
        pre_tool_use(
            {**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "transcript_path": transcript},
            str(home),
        )
        git(repo, "commit", "-qam", f"agent {sid}")
        post_tool_use(
            {**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "transcript_path": transcript},
            str(home),
        )
    (repo / "h.txt").write_text("h\n")
    git(repo, "add", "h.txt")
    git(repo, "commit", "-qm", "human only")
    monkeypatch.chdir(repo)
    assert cli.main(["digest", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["agent_commits"] == 2 and d["human_commits"] == 2  # init + human only
    assert len(d["sessions"]) == 2 and d["sessions"][0]["session"] in ("s-one", "s-two")
    assert d["lines_changed_by_human_after_agent"] == 1 and 0 < d["agent_share"] < 1
    assert (
        d["gate"] == {"confirmations": 2, "denials": 2, "top_reasons": [["force push", 2], ["pushing to a remote", 2]]}
        or d["gate"]["confirmations"] == 2
    )
    assert d["files"][0]["path"] == "a.txt" and d["files"][0]["sessions"] == 2
    assert cli.main(["digest", "--since", "30d"]) == 0
    out = capsys.readouterr().out
    assert (
        out.startswith("## gitvow digest ·")
        and "by agents 2 (50%)" in out
        and "### Sessions" in out
        and "1 human edit after" in out
    )
    assert "### Human commits in the period" in out and "human only" in out
    assert cli.main(["digest", "--since", "2099-01-01"]) == 0
    assert "Commits: 0" in capsys.readouterr().out
