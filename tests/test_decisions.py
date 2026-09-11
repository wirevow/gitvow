import json
import re
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
    assert d.deferred and d.findings[0]["finding"] == "run make (deploys)" and d.findings[0]["kind"] == "command"
    # The arguments are evidence, not identity: the same command with different arguments is one finding, so
    # a person answers it once and it can reach a rule threshold like every other kind.
    assert d.findings[0]["evidence"] == ["make deploy staging"]
    other = evaluate(pol, "Bash", {"command": "make deploy production --wait"})
    assert other.findings[0]["finding"] == d.findings[0]["finding"]
    assert other.findings[0]["evidence"] == ["make deploy production --wait"]
    # ...but two rules that happen to match the same program stay two findings, because a precedent set for
    # one consequence must not answer the other.
    pol["bash_confirm"].append({"pattern": "^make release", "reason": "cuts a release", "when": "commit"})
    assert evaluate(pol, "Bash", {"command": "make release"}).findings[0]["finding"] == "run make (cuts a release)"
    # The program is the one the rule matched, not the first word of the line: `cd x && kubectl ...` is a
    # kubectl finding, or a chained command would be a way to walk past the record entirely.
    pol["bash_confirm"] = [{"pattern": r"\bkubectl\s+apply\b", "reason": "cluster mutation", "when": "commit"}]
    chained = evaluate(pol, "Bash", {"command": "cd svc && kubectl apply -f b.yaml"})
    assert chained.findings[0]["finding"] == "run kubectl (cluster mutation)"
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
        "to": None,
        "note": "reviewed",
    }
    # post-commit moved the findings out; the note carries them
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"] == [] and st["last_commit"]["sha"] == git(repo, "rev-parse", "HEAD")
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "2 decisions recorded" in msg
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 6 and len(note["decisions"]) == 2
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
    assert r["decisions"] == {
        "accepted": 1,
        "declined": 0,
        "open": 1,
        "referred": 0,
        "reopened": 1,
        "pre_answered": 0,
    }
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


def test_proposal_recorded_and_revisit_keeps_both(repo, home, payload, transcript, monkeypatch, capsys):
    f = "edit core/authz_rules.go"
    (repo / "a.txt").write_text("p\n")
    git(repo, "commit", "-qam", f"prior\n\nGitvow-Accepted: {f} by nikhil")
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 2 and "Proposed: accept." in msg
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"][0]["proposed"] == "accept"
    assert '"kind": "card", "findings": 1, "proposed": 1' in (repo / ".git" / "gitvow-hooks.log").read_text()
    dec.decide(str(repo), "1", "accept", {}, user_turns=2)
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    _install_hooks(repo)
    (repo / "a.txt").write_text("q\n")
    git(repo, "commit", "-qam", "agent")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["decisions"][0]["proposed"] == "accept"
    base = git(repo, "rev-parse", "HEAD~1")
    r = build(str(repo), base, "HEAD")
    assert r["decisions"]["pre_answered"] == 1 and "matched the record's proposal" in render_markdown(r)
    # a human commit leaves an open finding; revisit closes the debt and keeps the earlier trailer
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": ".github/workflows/ci.yml"}), str(home))
    (repo / "a.txt").write_text("r\n")
    git(repo, "commit", "-qam", "human")
    open_sha = git(repo, "rev-parse", "--short", "HEAD")
    assert dec.open_debt(str(repo)) == [
        {"sha": open_sha, "date": dec.open_debt(str(repo))[0]["date"], "finding": "edit .github/workflows/ci.yml"}
    ]
    monkeypatch.chdir(repo)
    assert cli.main(["revisit", open_sha]) == 0
    assert "1. open: edit .github/workflows/ci.yml" in capsys.readouterr().out
    assert cli.main(["revisit", open_sha, "accept", "--reason", "seen it"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == f"Gitvow-Accepted: edit .github/workflows/ci.yml by t: seen it (revisits {open_sha})"
    body = git(repo, "log", "-1", "--format=%B")
    assert f"Gitvow-Revisits: {git(repo, 'rev-parse', open_sha)}" in body and "Was: open" in body
    assert "Gitvow-Open: edit .github/workflows/ci.yml" in git(repo, "log", "-1", "--format=%B", open_sha)
    assert dec.open_debt(str(repo)) == []
    assert git(repo, "diff", "HEAD~1", "HEAD") == ""  # an empty commit
    assert cli.main(["revisit", "HEAD", "decline", "--finding", "9"]) == 1
    assert "no decision 9" in capsys.readouterr().err
    # history now counts the revisit as the latest answer on that finding
    assert dec.history(str(repo), "edit .github/workflows/ci.yml")[0]["answer"] == "accepted"


def test_a_referral_is_not_an_answer_and_not_debt(repo, home, payload, transcript, monkeypatch, capsys):
    """ "Nobody decided" and "you asked the wrong person" need different remedies, so they are different states."""
    from gitvow import digest as dg
    from gitvow.rules import derive

    base = git(repo, "rev-parse", "HEAD")
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 2 and "gitvow decide <n> refer" in msg  # the card offers the third answer
    monkeypatch.chdir(repo)
    assert cli.main(["decide", "1", "refer", "--to", "security", "--reason", "not my call"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == "Gitvow-Referred: edit core/authz_rules.go by t to=security: not my call"
    assert "recorded as a referral, not an answer" in err
    # a referral closes the card: holding the commit hostage to the wrong person answers nothing
    assert dec.undecided(str(repo)) == []
    assert "[referred by t, to security]" in dec.card(str(repo), for_agent=False)
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 0
    _install_hooks(repo)
    (repo / "a.txt").write_text("ref\n")
    git(repo, "commit", "-qam", "agent commit")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Referred: edit core/authz_rules.go by t to=security: not my call" in body
    t = dec.parse_trailers(body)[0]
    assert t["answer"] == "referred" and t["to"] == "security" and t["scope"] is None
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["decisions"][0]["answer"] == "referred" and note["decisions"][0]["to"] == "security"
    # separate from open debt everywhere the remediation differs
    ref = git(repo, "rev-parse", "--short", "HEAD")
    assert dec.open_debt(str(repo)) == []
    assert dec.referrals(str(repo)) == [
        {
            "sha": ref,
            "date": dec.referrals(str(repo))[0]["date"],
            "finding": "edit core/authz_rules.go",
            "to": "security",
            "by": "t",
        }
    ]
    r = build(str(repo), base, "HEAD")
    assert r["decisions"]["referred"] == 1 and r["decisions"]["open"] == 0
    md = render_markdown(r)
    assert "**1 referred to someone else**" in md
    assert "**Referred to security:** edit core/authz_rules.go — t was not the person to decide this" in md
    assert "it needs a different person, not a reminder" in md
    assert "Gitvow-Referred: edit core/authz_rules.go by t to=security: not my call" in decisions_summary(r)
    d = dg.build(str(repo), "30d")
    assert d["decisions"]["referred"] == 1 and d["decisions"]["referrals"] == 1 and d["decisions"]["debt"] == 0
    rendered = dg.render(d)
    assert "1 referred" in rendered and "awaiting a different person 1" in rendered
    assert "### Referred, waiting on someone else" in rendered and "→ security" in rendered
    assert "### Decision debt" not in rendered
    # three referrals are not a precedent, whoever they were aimed at
    pol = load_policy(str(repo))
    for i in range(3):
        (repo / "a.txt").write_text(f"r{i}\n")
        git(repo, "commit", "-qam", f"r{i}\n\nGitvow-Referred: edit core/authz_rules.go by t to=security")
    assert dec.history_all(str(repo), pol=pol) == []
    assert derive(str(repo), pol)["candidates"] == [] and derive(str(repo), pol)["proposals"] == []
    # the person it was routed to answers it, and the referral stops waiting
    assert cli.main(["revisit", ref, "accept", "--by", "security", "--reason", "checked"]) == 0
    capsys.readouterr()
    assert ref not in [x["sha"] for x in dec.referrals(str(repo))]  # answered, so it stops waiting
    assert len(dec.referrals(str(repo))) == 3  # the three later ones are still waiting on security
    assert dec.history(str(repo), "edit core/authz_rules.go")[0]["by"] == "security"
    # and a referral can be recorded against a finding already on the branch
    assert cli.main(["revisit", "HEAD", "refer", "--to", "platform"]) == 0
    assert "to=platform" in capsys.readouterr().out
    assert dec.referrals(str(repo))[0]["to"] == "platform"


def test_the_grammar_grows_by_trailer_name_never_by_a_tail_token():
    """A new trailer name is invisible to an older parser. A new tail token on a shipped name is not.

    Pinned because it is a constraint on every future addition, and because the failure is silent: an older
    gitvow reading a token it does not know backtracks it into the finding and reports nothing.
    """
    v0_15 = re.compile(r"^Gitvow-(Accepted|Declined|Open):\s*(.+?)(?: by (\S+))?(?: scope=(\S+))?(?:: (.*))?$", re.M)
    # The safe direction, and the one 0.16 took: 0.15 does not match `Gitvow-Referred` at all, so a commit
    # carrying one reads there exactly as it would have before the name existed.
    assert v0_15.search("Gitvow-Referred: edit foo.go by nikhil to=security") is None
    # The unsafe direction: a token appended to a name 0.15 already knows is swallowed by the lazy subject.
    m = v0_15.search("Gitvow-Accepted: edit foo.go class=k7 by nikhil")
    assert m is not None and m.group(2) == "edit foo.go class=k7"
    # Today's parser has the same shape, so the forked identity is what any future token would cost.
    assert dec.parse_trailers("Gitvow-Accepted: edit foo.go class=k7 by nikhil")[0]["finding"] != "edit foo.go"
