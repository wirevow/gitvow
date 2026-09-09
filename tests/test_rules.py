import json
from pathlib import Path

from gitvow import cli
from gitvow import decisions as dec
from gitvow.hooks import pre_tool_use, session_start
from gitvow.policy import DEFAULT_POLICY_PATH
from gitvow.rules import END, START, derive, render, write_section


def _decided_commit(repo, subject, trailer, date="2026-09-01T12:00:00"):
    (repo / "a.txt").write_text(subject + "\n")
    import subprocess

    subprocess.run(
        ["git", "commit", "-qam", f"{subject}\n\n{trailer}"],
        cwd=repo,
        env={**__import__("os").environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date},
        check=True,
    )


def _pol():
    return json.loads(Path(DEFAULT_POLICY_PATH).read_text())


def test_rules_need_threshold_authority_and_recency(repo):
    pol = _pol()
    f = "edit core/authz_rules.go"
    _decided_commit(repo, "c1", f"Gitvow-Accepted: {f} by nikhil scope=staging", "2026-06-01T12:00:00")
    _decided_commit(repo, "c2", f"Gitvow-Accepted: {f} by nikhil", "2026-06-10T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert d["rules"] == [] and d["candidates"][0]["count"] == 2 and d["candidates"][0]["answer"] == "accepted"
    _decided_commit(repo, "c3", f"Gitvow-Accepted: {f} by priya", "2026-06-15T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    r = d["rules"][0]
    assert r["count"] == 3 and r["by"] == ["nikhil", "priya"] and r["scopes"] == ["staging"]
    assert r["first"] == "2026-06-01" and r["last"] == "2026-06-15" and r["expires"] == "2026-09-13"
    # decayed after the window
    d = derive(str(repo), pol, today="2026-09-20")
    assert d["rules"] == [] and d["decayed"][0]["finding"] == f
    # a contradiction resets the run
    _decided_commit(repo, "c4", f"Gitvow-Declined: {f} by nikhil: not any more", "2026-06-16T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert (
        d["rules"] == [] and d["candidates"][0]["answer"] == "declined" and d["candidates"][0]["contradicted_by"] == 3
    )
    # only authorities count once the policy names them
    pol["decisions"]["authorities"] = ["security@example.com"]
    _decided_commit(repo, "c5", f"Gitvow-Declined: {f} by security", "2026-06-17T12:00:00")
    _decided_commit(repo, "c6", f"Gitvow-Declined: {f} by security", "2026-06-18T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert d["rules"] == [] and d["candidates"][0]["count"] == 2  # nikhil's decline is data, not a rule
    pol["decisions"]["rule_threshold"] = 2
    assert derive(str(repo), pol, today="2026-06-20")["rules"][0]["answer"] == "declined"


def test_render_and_managed_section(tmp_path):
    d = {
        "threshold": 3,
        "decay_days": 90,
        "rules": [
            {
                "finding": "route /v1/x in api.py",
                "answer": "declined",
                "count": 4,
                "first": "2026-05-01",
                "last": "2026-06-01",
                "by": ["nikhil"],
                "scopes": [],
                "expires": "2026-08-30",
            }
        ],
        "candidates": [{"finding": "edit ci.yml", "answer": "accepted", "count": 1, "last": "2026-06-02", "by": ["t"]}],
        "decayed": [],
    }
    block = render(d)
    assert block.startswith(START) and block.rstrip().endswith(END)
    assert (
        "has been declined 4 times" in block
        and "Propose the alternative first" in block
        and "Decays 2026-08-30" in block
    )
    assert "Not yet rules" not in block and "Not yet rules" in render(d, for_agent=False)
    p = tmp_path / "CLAUDE.md"
    p.write_text("# Project\n\nRead me.\n")
    assert write_section(str(p), block).startswith("added")
    assert p.read_text().startswith("# Project\n\nRead me.\n\n" + START)
    d["rules"][0]["count"] = 5
    assert write_section(str(p), render(d)).startswith("updated")
    assert p.read_text().count(START) == 1 and "declined 5 times" in p.read_text()
    assert write_section(str(p), "").startswith("removed")
    assert START not in p.read_text() and p.read_text().startswith("# Project")
    assert render({"rules": [], "candidates": [], "decayed": [], "threshold": 3, "decay_days": 90}) == ""


def test_card_and_session_start_carry_the_rule(repo, home, payload, monkeypatch, capsys):
    f = "edit core/authz_rules.go"
    for i in range(3):
        _decided_commit(repo, f"c{i}", f"Gitvow-Accepted: {f} by nikhil", f"2026-09-0{i + 1}T12:00:00")
    code, msg = session_start(payload("SessionStart"), str(home))
    assert code == 0 and START in msg and "accepted 3 times" in msg
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert code == 2 and "rule: accepted 3 times by authorities since 2026-09-01; decays 2026-12-02." in msg
    assert "Proposed: accept." in msg
    monkeypatch.chdir(repo)
    assert cli.main(["rules"]) == 0
    assert "earned rule" in capsys.readouterr().out
    assert cli.main(["rules", "--write"]) == 0
    assert "added managed section" in capsys.readouterr().out and START in (repo / "CLAUDE.md").read_text()
    assert cli.main(["rules", "--write", "--agent", "codex"]) == 0
    assert START in (repo / "AGENTS.md").read_text()
    capsys.readouterr()
    assert cli.main(["rules", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rules"][0]["count"] == 3
    # the hook command prints SessionStart context on stdout for Claude Code
    import io
    import sys

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload("SessionStart"))))
    assert cli.main(["hook", "SessionStart"]) == 0
    out = capsys.readouterr().out
    assert START in out and "still put it to the person" in out


def test_no_rules_message(repo, home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["rules"]) == 0
    assert "no earned rules yet" in capsys.readouterr().out
    assert dec.history_all(str(repo)) == []
