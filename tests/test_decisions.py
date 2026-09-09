import json
from pathlib import Path

import pytest

from gitvow import cli
from gitvow import decisions as dec
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.policy import PolicyError, evaluate, load_policy
from gitvow.report import build, decisions_summary, render_markdown
from tests.conftest import git


def _install_hooks(repo):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")


def test_when_defaults_and_validation(tmp_path):
    pol = load_policy(str(tmp_path), str(tmp_path))
    d = evaluate(pol, "Edit", {"file_path": "svc/values/production-in/app/values.yaml"})
    assert d.outcome == "confirm" and d.when == "commit" and not d.blocks and d.deferred
    assert d.findings[0]["finding"] == "edit svc/values/production-in/app/values.yaml"
    d = evaluate(pol, "Edit", {"file_path": ".gitvow/policy.json"})
    assert d.blocks and d.when == "immediate"  # loosening the gate is never deferred
    d = evaluate(pol, "Bash", {"command": "git push origin x"})
    assert d.blocks and d.when == "immediate"
    pol["bash_confirm"] = [{"pattern": "^make deploy", "reason": "deploys", "when": "commit"}]
    d = evaluate(pol, "Bash", {"command": "make deploy staging"})
    assert d.deferred and d.findings[0]["finding"] == "run make deploy staging" and d.findings[0]["kind"] == "command"
    (tmp_path / ".gitvow").mkdir()
    (tmp_path / ".gitvow" / "policy.json").write_text(json.dumps({"path_confirm": [{"pattern": "x", "when": "later"}]}))
    with pytest.raises(PolicyError):
        load_policy(str(tmp_path), str(tmp_path))
    (tmp_path / ".gitvow" / "policy.json").write_text(json.dumps({"decisions": {"mode": "loud"}}))
    with pytest.raises(PolicyError):
        load_policy(str(tmp_path), str(tmp_path))


def test_findings_accumulate_then_card_then_trailers_and_note(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    edit = payload("PreToolUse", "Edit", {"file_path": str(repo / "core/authz_rules.go")}, transcript)
    assert pre_tool_use(edit, str(home)) == (0, "")
    assert pre_tool_use(edit, str(home)) == (0, "")  # same finding again only counts
    ci = payload("PreToolUse", "Write", {"file_path": ".github/workflows/ci.yml"}, transcript)
    assert pre_tool_use(ci, str(home)) == (0, "")
    fs = dec.open_findings(str(repo))
    assert [f["finding"] for f in fs] == ["edit core/authz_rules.go", "edit .github/workflows/ci.yml"]
    assert fs[0]["raised"] == 2 and fs[0]["decision"] is None
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert log.count('"kind": "finding"') == 3
    # the commit is refused once, with the card
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 2 and "DECISIONS REQUIRED before this commit: 2 findings" in msg
    assert "1. edit core/authz_rules.go" in msg and "raised 2 times" in msg and "record: no earlier decision." in msg
    assert '"kind": "card"' in (repo / ".git" / "gitvow-hooks.log").read_text()
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["card_user_turns"] == 1  # the fixture transcript has one user message
    # a person answers; the agent records it
    done = dec.decide(str(repo), "1", "accept", {}, scope="staging", reason="reviewed", user_turns=2)
    assert done[0]["decision"]["by"] == "t" and done[0]["decision"]["authority"] == "commit-access"
    dec.decide(str(repo), "2", "decline", {"decisions": {"authorities": ["someone-else"]}}, user_turns=2)
    assert dec.open_findings(str(repo))[1]["decision"]["authority"] == "none"
    assert dec.undecided(str(repo)) == []
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0
    _install_hooks(repo)
    (repo / "a.txt").write_text("changed\n")
    git(repo, "commit", "-qam", "feature")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: sess-1" in body
    assert "Gitvow-Accepted: edit core/authz_rules.go by t scope=staging: reviewed" in body
    assert "Gitvow-Declined: edit .github/workflows/ci.yml by t" in body
    assert dec.parse_trailers(body)[0] == {
        "answer": "accepted",
        "finding": "edit core/authz_rules.go",
        "by": "t",
        "scope": "staging",
        "note": "reviewed",
    }
    # post-commit moved the findings out; the note carries them
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"] == [] and st["last_commit"]["sha"] == git(repo, "rev-parse", "HEAD")
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "2 decisions recorded" in msg
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 5 and len(note["decisions"]) == 2
    d0 = note["decisions"][0]
    assert d0["answer"] == "accepted" and d0["scope"] == "staging" and d0["human_turns_after_card"] == 1
    assert note["decisions"][1]["authority"] == "none"
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert "last_commit" not in st and "card_user_turns" not in st
    # the next card on the same finding carries the record
    assert pre_tool_use(edit, str(home)) == (0, "")
    card = dec.card(str(repo))
    assert "accepted 1 time, last accepted by t on" in card and "(scope staging). Proposed: accept." in card


def test_human_commit_records_open_and_strict_refuses(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    assert pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}), str(home)) == (0, "")
    _install_hooks(repo)
    (repo / "a.txt").write_text("h\n")
    out = git(repo, "commit", "-qam", "human commit while a finding is open")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session" not in body and "Gitvow-Open: edit core/authz_rules.go" in body
    assert out == ""  # commit went through
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"] == [] and st["last_commit"]["findings"][0]["decision"] is None
    # strict mode: refused until decided
    from gitvow.policy import DEFAULT_POLICY_PATH

    strict = json.loads(Path(DEFAULT_POLICY_PATH).read_text())
    strict["decisions"]["mode"] = "strict"
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(strict))
    assert pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}), str(home)) == (0, "")
    (repo / "a.txt").write_text("h2\n")
    import subprocess

    r = subprocess.run(["git", "commit", "-qam", "strict"], cwd=repo, capture_output=True, text=True)
    assert r.returncode != 0 and "decisions.mode is strict" in r.stderr and "1. edit core/authz_rules.go" in r.stderr
    assert git(repo, "log", "-1", "--format=%s") == "human commit while a finding is open"
    dec.decide(str(repo), "all", "accept", {})
    r = subprocess.run(["git", "commit", "-qam", "strict ok"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0
    assert "Gitvow-Accepted: edit core/authz_rules.go by t" in git(repo, "log", "-1", "--format=%B")


def test_cli_decisions_and_decide(repo, home, payload, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    session_start(payload("SessionStart"), str(home))
    assert cli.main(["decisions"]) == 0 and "No open findings" in capsys.readouterr().out
    assert cli.main(["decide", "1", "accept"]) == 1
    assert "no open findings" in capsys.readouterr().err
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}), str(home))
    assert cli.main(["decisions"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1. edit core/authz_rules.go") and "DECISIONS REQUIRED" not in out
    assert cli.main(["decide", "9", "accept"]) == 1
    assert cli.main(["decide", "1", "accept", "--scope", "staging", "--reason", "ok", "--by", "Priya Nair"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "Gitvow-Accepted: edit core/authz_rules.go by Priya-Nair scope=staging: ok"
    assert cli.main(["decisions", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["decision"]["scope"] == "staging"


def test_report_lists_decisions_reopens_scope_and_summarises(repo, home, payload, transcript, monkeypatch, capsys):
    base = git(repo, "rev-parse", "HEAD")
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 2
    dec.decide(str(repo), "1", "accept", {}, scope="staging", reason="staging only", user_turns=1)  # no new user turn
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 0
    _install_hooks(repo)
    (repo / "a.txt").write_text("x\n")
    git(repo, "commit", "-qam", "agent commit")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": ".github/workflows/ci.yml"}), str(home))
    (repo / "a.txt").write_text("y\n")
    git(repo, "commit", "-qam", "human commit")  # Gitvow-Open
    r = build(str(repo), base, "HEAD", target="main")
    assert r["decisions"] == {"accepted": 1, "declined": 0, "open": 1, "reopened": 1}
    md = render_markdown(r)
    assert "**Decisions:** 1 accepted · 0 declined · **1 open** · **1 to reopen for main**" in md
    assert "accepted for staging; this pull request targets main. Accept for main?" in md
    assert "**Open:** edit .github/workflows/ci.yml" in md
    assert "answered without a user message" in md  # card_user_turns 1, decided at 1 user turn
    assert build(str(repo), base, "HEAD", target="staging")["decisions"]["reopened"] == 0
    summary = decisions_summary(r)
    assert summary.startswith("<!-- gitvow-decisions -->\n")
    assert "Gitvow-Accepted: edit core/authz_rules.go by t scope=staging: staging only\n" in summary
    assert "Gitvow-Open: edit .github/workflows/ci.yml\n<!-- /gitvow-decisions -->" in summary
    monkeypatch.chdir(repo)
    assert cli.main(["report", "--base", base, "--decisions-summary"]) == 0
    assert capsys.readouterr().out == summary
