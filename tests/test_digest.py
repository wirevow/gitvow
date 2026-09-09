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


def test_digest_decisions_questions_and_payback(repo, home, payload, transcript, monkeypatch, capsys):
    from gitvow import decisions as dec
    from gitvow.digest import build, render
    from gitvow.hooks import post_tool_use, pre_tool_use, session_start
    from gitvow.install import _write_git_hook
    from gitvow.state import log_event

    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 2
    dec.decide(str(repo), "1", "accept", {}, user_turns=2)
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("x\n")
    git(repo, "commit", "-qam", "agent")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": ".github/workflows/ci.yml"}), str(home))
    (repo / "a.txt").write_text("y\n")
    git(repo, "commit", "-qam", "human")
    log_event(str(repo), "restore", {"ref": "refs/gitvow/snapshots/sess-1/1"})
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push origin main"}), str(home))  # immediate
    d = build(str(repo), "1d")
    assert d["decisions"]["accepted"] == 1 and d["decisions"]["open"] == 1 and d["decisions"]["debt"] == 1
    assert (
        d["decisions"]["rules_in_force"] == 0
        and d["decisions"]["debt_items"][0]["finding"] == "edit .github/workflows/ci.yml"
    )
    assert d["questions"]["cards"] == 1 and d["questions"]["sessions"] == 1 and d["questions"]["per_session"] == 1.0
    assert d["questions"]["findings"] == 2 and d["questions"]["immediate"] == 1
    assert d["payback"] == {"snapshots_restored": 1, "pre_answered": 0, "answers_matching_proposal": 0}
    text = render(d)
    assert "Decisions: 1 accepted · 0 declined · 1 open · decision debt 1" in text
    assert (
        "Questions: 1 card over 1 session (1.00 per session) · 2 findings collected · 1 immediate confirmation" in text
    )
    assert (
        "Payback: 1 snapshot restored · 0 questions pre-answered by the record · 0 answers matched the proposal" in text
    )
    assert "### Decision debt" in text and "gitvow revisit" in text
