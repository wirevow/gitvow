import json
from pathlib import Path

from gitvow import cli
from gitvow import decisions as dec
from gitvow.hooks import pre_tool_use, session_start
from gitvow.policy import DEFAULT_POLICY_PATH
from gitvow.rules import END, RULES_NOTES_REF, START, decide_proposals, derive, render, select, write_section
from tests.conftest import git


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


def test_threshold_authority_and_recency_make_a_proposal_not_a_rule(repo):
    pol = _pol()
    f = "edit core/authz_rules.go"
    _decided_commit(repo, "c1", f"Gitvow-Accepted: {f} by nikhil", "2026-06-01T12:00:00")
    _decided_commit(repo, "c2", f"Gitvow-Accepted: {f} by nikhil", "2026-06-10T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert d["rules"] == [] and d["proposals"] == []
    assert d["candidates"][0]["count"] == 2 and d["candidates"][0]["answer"] == "accepted"
    _decided_commit(repo, "c3", f"Gitvow-Accepted: {f} by priya", "2026-06-15T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    # the threshold produces a proposal; nothing is a rule until a person accepts it
    assert d["rules"] == []
    r = d["proposals"][0]
    assert r["count"] == 3 and r["by"] == ["nikhil", "priya"] and r["exceptions"] == 0
    assert r["first"] == "2026-06-01" and r["last"] == "2026-06-15" and r["expires"] == "2026-09-13"
    assert [e["date"] for e in r["evidence"]] == ["2026-06-01", "2026-06-10", "2026-06-15"]
    # decayed after the window, proposal or not
    d = derive(str(repo), pol, today="2026-09-20")
    assert d["rules"] == [] and d["proposals"] == [] and d["decayed"][0]["finding"] == f
    # a contradiction resets the run
    _decided_commit(repo, "c4", f"Gitvow-Declined: {f} by nikhil: not any more", "2026-06-16T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert (
        d["proposals"] == []
        and d["candidates"][0]["answer"] == "declined"
        and d["candidates"][0]["contradicted_by"] == 3
    )
    # only authorities count once the policy names them
    pol["decisions"]["authorities"] = ["security@example.com"]
    _decided_commit(repo, "c5", f"Gitvow-Declined: {f} by security", "2026-06-17T12:00:00")
    _decided_commit(repo, "c6", f"Gitvow-Declined: {f} by security", "2026-06-18T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    assert d["proposals"] == [] and d["candidates"][0]["count"] == 2  # nikhil's decline is data, not a proposal
    pol["decisions"]["rule_threshold"] = 2
    assert derive(str(repo), pol, today="2026-06-20")["proposals"][0]["answer"] == "declined"


def test_a_scoped_answer_is_an_exception_and_never_counts(repo):
    """Three accepts of an exception are three exceptions. They must not add up to a precedent."""
    pol = _pol()
    f = "edit core/authz_rules.go"
    for i, day in enumerate(("01", "02", "03")):
        _decided_commit(repo, f"s{i}", f"Gitvow-Accepted: {f} by nikhil scope=staging", f"2026-06-{day}T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    # nothing at all: a finding answered only under a scope has no unscoped run to count
    assert d["rules"] == [] and d["proposals"] == [] and d["candidates"] == []
    _decided_commit(repo, "u1", f"Gitvow-Accepted: {f} by nikhil", "2026-06-04T12:00:00")
    _decided_commit(repo, "u2", f"Gitvow-Accepted: {f} by priya", "2026-06-05T12:00:00")
    d = derive(str(repo), pol, today="2026-06-20")
    c = d["candidates"][0]
    assert c["count"] == 2 and c["exceptions"] == 3 and c["exception_scopes"] == ["staging"]
    assert d["proposals"] == []  # 2 unscoped + 3 scoped is not 5
    # a scoped answer of the opposite kind does not break the run either: it was never about the general case
    _decided_commit(repo, "s3", f"Gitvow-Declined: {f} by nikhil scope=prod", "2026-06-06T12:00:00")
    _decided_commit(repo, "u3", f"Gitvow-Accepted: {f} by nikhil", "2026-06-07T12:00:00")
    p = derive(str(repo), pol, today="2026-06-20")["proposals"][0]
    assert p["count"] == 3 and p["exceptions"] == 4 and p["exception_scopes"] == ["prod", "staging"]
    assert p["scopes"] == []  # a rule can never carry a scope


def test_an_authority_accepts_the_proposal_and_the_acceptance_is_recorded(repo, home, monkeypatch, capsys):
    pol = _pol()
    f = "edit core/authz_rules.go"
    for i in range(3):
        _decided_commit(repo, f"c{i}", f"Gitvow-Accepted: {f} by nikhil", f"2026-09-0{i + 1}T12:00:00")
    monkeypatch.chdir(repo)
    assert cli.main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "Proposed, not rules (1)" in out and "nobody has accepted it" in out
    assert "earned rule" not in out and "gitvow rules accept" in out
    # someone the policy does not name cannot create precedent, even though their own answers would count
    named = _pol()
    named["decisions"]["authorities"] = ["nikhil"]  # the committer is 't', so 't' has no standing here
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(named))
    assert cli.main(["rules", "accept", "1"]) == 1
    assert "not named under decisions.authorities" in capsys.readouterr().err
    assert derive(str(repo), named, today="2026-09-05")["proposals"][0]["count"] == 3  # still only a proposal
    (repo / ".gitvow" / "policy.json").unlink()
    assert cli.main(["rules", "accept", "1", "--reason", "this is our pattern"]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == f"Gitvow-Rule-Accepted: {f} by t answer=accepted: this is our pattern"
    assert "in force from now on" in err
    d = derive(str(repo), pol, today="2026-09-05")
    assert d["proposals"] == [] and d["rules"][0]["accepted_by"] == "t"
    assert d["rules"][0]["accepted_on"] == derive(str(repo), pol)["rules"][0]["accepted_on"]
    # the acceptance carries its evidence in a note of its own, the way a decision does
    note = git(repo, "notes", f"--ref={RULES_NOTES_REF}", "show", "HEAD")
    assert note.startswith("gitvow-rule-decision")
    data = json.loads(note.split("\n", 1)[1])
    assert data["schema"] == 1 and data["verdict"] == "accepted" and data["authority"] == "commit-access"
    assert data["reason"] == "this is our pattern" and data["threshold"] == 3
    assert len(data["rules"][0]["evidence"]) == 3 and data["rules"][0]["by"] == ["nikhil"]
    assert git(repo, "diff", "HEAD~1", "HEAD") == ""  # an empty commit
    assert cli.main(["show", "HEAD"]) == 0
    assert "gitvow-rule-decision" in capsys.readouterr().out  # `gitvow show` finds the rules ref
    # accepting for one answer does not carry over when a contradiction flips the run
    _decided_commit(repo, "flip", f"Gitvow-Declined: {f} by nikhil", "2026-09-06T12:00:00")
    _decided_commit(repo, "flip2", f"Gitvow-Declined: {f} by nikhil", "2026-09-07T12:00:00")
    _decided_commit(repo, "flip3", f"Gitvow-Declined: {f} by nikhil", "2026-09-08T12:00:00")
    d = derive(str(repo), pol, today="2026-09-10")
    assert d["rules"] == [] and d["proposals"][0]["answer"] == "declined"


def test_a_rejected_proposal_waits_for_fresh_evidence(repo, home, monkeypatch, capsys):
    pol = _pol()
    f = "edit core/authz_rules.go"
    for i in range(3):
        _decided_commit(repo, f"c{i}", f"Gitvow-Accepted: {f} by nikhil", f"2026-09-0{i + 1}T12:00:00")
    monkeypatch.chdir(repo)
    for var in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(var, "2026-09-04T12:00:00")  # date the rejection, so "after it" is testable
    assert cli.main(["rules", "reject", f, "--reason", "one-off, not policy"]) == 0
    assert f"Gitvow-Rule-Rejected: {f} by t answer=accepted" in capsys.readouterr().out
    d = derive(str(repo), pol, today="2026-09-04")
    assert d["rules"] == [] and d["proposals"] == []
    r = d["rejected"][0]
    assert r["rejected_by"] == "t" and r["rejected_reason"] == "one-off, not policy" and r["fresh"] == 0
    # one more accept does not re-propose it: a rejection is not overturned by re-counting old evidence
    _decided_commit(repo, "c4", f"Gitvow-Accepted: {f} by nikhil", "2026-09-05T12:00:00")
    d = derive(str(repo), pol, today="2026-09-06")
    assert d["proposals"] == [] and d["rejected"][0]["fresh"] == 1
    assert cli.main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "Rejected, awaiting fresh evidence" in out and "1 answer since" in out
    # a full threshold of answers dated after the rejection brings it back
    _decided_commit(repo, "c5", f"Gitvow-Accepted: {f} by nikhil", "2026-09-06T12:00:00")
    _decided_commit(repo, "c6", f"Gitvow-Accepted: {f} by priya", "2026-09-07T12:00:00")
    d = derive(str(repo), pol, today="2026-09-08")
    assert d["rejected"] == [] and d["proposals"][0]["fresh"] == 3
    assert "rests on 3 answers since" in render(d, for_agent=False)
    assert cli.main(["rules", "accept", "--all"]) == 0
    capsys.readouterr()
    assert len(derive(str(repo), pol, today="2026-09-08")["rules"]) == 1


def test_select_rejects_what_it_cannot_name(repo):
    d = {"proposals": [{"finding": "edit a"}], "threshold": 3}
    assert select(d, "1", False) == d["proposals"]
    assert select(d, "edit a", False) == d["proposals"]
    assert select(d, None, True) == d["proposals"]
    for which, take_all, msg in (
        (None, False, "say which"),
        ("9", False, "no proposal 9"),
        ("edit b", False, "edit b"),
    ):
        try:
            select(d, which, take_all)
            raise AssertionError("should have refused")
        except ValueError as e:
            assert msg in str(e)
    try:
        select({"proposals": [], "threshold": 3}, None, True)
        raise AssertionError("should have refused")
    except ValueError as e:
        assert "no proposals standing" in str(e)
    try:
        decide_proposals(str(repo), {}, d["proposals"], "maybe")
        raise AssertionError("should have refused")
    except ValueError as e:
        assert "verdict must be" in str(e)


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
                "accepted_by": "priya",
                "accepted_on": "2026-06-02",
                "expires": "2026-08-30",
            }
        ],
        "proposals": [
            {
                "finding": "edit ci.yml",
                "answer": "accepted",
                "count": 3,
                "first": "2026-06-01",
                "last": "2026-06-03",
                "by": ["t"],
                "exceptions": 2,
            }
        ],
        "rejected": [],
        "candidates": [
            {
                "finding": "edit values.yaml",
                "answer": "accepted",
                "count": 1,
                "last": "2026-06-02",
                "by": ["t"],
                "exceptions": 4,
            }
        ],
        "decayed": [],
    }
    block = render(d)
    assert block.startswith(START) and block.rstrip().endswith(END)
    assert "has been declined 4 times" in block and "accepted as a rule by priya on 2026-06-02" in block
    assert "Propose the alternative first" in block and "Decays 2026-08-30" in block
    # a proposal reaches the agent labelled, and the label says it grants nothing
    assert "Proposed, not rules (1)" in block and "**Nobody has accepted it.**" in block
    assert "it grants nothing" in block and "carried a scope and was set aside" in block
    assert "- `edit ci.yml` was accepted 3 times" in block  # unnumbered for an instruction file
    human = render(d, for_agent=False)
    assert "1. `edit ci.yml` was accepted 3 times" in human  # numbered for `gitvow rules accept <n>`
    assert "Not yet proposed" not in block and "Not yet proposed" in human
    assert "4 scoped exception(s) set aside" in human
    p = tmp_path / "CLAUDE.md"
    p.write_text("# Project\n\nRead me.\n")
    assert write_section(str(p), block).startswith("added")
    assert p.read_text().startswith("# Project\n\nRead me.\n\n" + START)
    d["rules"][0]["count"] = 5
    assert write_section(str(p), render(d)).startswith("updated")
    assert p.read_text().count(START) == 1 and "declined 5 times" in p.read_text()
    assert write_section(str(p), "").startswith("removed")
    assert START not in p.read_text() and p.read_text().startswith("# Project")
    empty = {"rules": [], "proposals": [], "rejected": [], "candidates": [], "decayed": [], "threshold": 3}
    assert render({**empty, "decay_days": 90}) == ""
    # a proposal alone still produces a block, so nothing that was visible before becomes silent
    assert START in render({**empty, "decay_days": 90, "proposals": d["proposals"]})


def test_card_and_session_start_label_a_proposal_and_carry_an_accepted_rule(repo, home, payload, monkeypatch, capsys):
    f = "edit core/authz_rules.go"
    for i in range(3):
        _decided_commit(repo, f"c{i}", f"Gitvow-Accepted: {f} by nikhil", f"2026-09-0{i + 1}T12:00:00")
    code, msg = session_start(payload("SessionStart"), str(home))
    assert code == 0 and START in msg and "Proposed, not rules (1)" in msg
    assert "accepted 3 times" in msg and "## What this repository has decided" not in msg
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert code == 2 and "proposed rule (NOT a rule: nobody has accepted it, it permits nothing)" in msg
    assert "   rule:" not in msg and "Proposed: accept." in msg
    monkeypatch.chdir(repo)
    assert cli.main(["rules", "accept", "1"]) == 0
    capsys.readouterr()
    code, msg = session_start(payload("SessionStart"), str(home))
    assert "## What this repository has decided (1 earned rule)" in msg and "Proposed, not rules" not in msg
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert code == 2 and "rule: accepted 3 times by authorities since 2026-09-01" in msg
    assert "accepted as a rule by t on" in msg and "proposed rule" not in msg
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
    assert "no earned rules and nothing proposed" in capsys.readouterr().out
    assert dec.history_all(str(repo)) == []
    _decided_commit(repo, "c1", "Gitvow-Accepted: edit x by nikhil", "2026-09-01T12:00:00")
    _decided_commit(repo, "c2", "Gitvow-Accepted: edit x by nikhil", "2026-09-02T12:00:00")
    assert cli.main(["rules"]) == 0
    out = capsys.readouterr().out
    assert "no earned rules and nothing proposed" in out
    assert "Not yet proposed (fewer than 3 consistent answers by authorities):" in out
    assert "- edit x: accepted 2 times (last 2026-09-02 by nikhil)" in out


def test_a_rule_decision_refuses_to_swallow_staged_work(repo, home, monkeypatch, capsys):
    """`git commit --allow-empty` commits the index, so a governance commit must not be a place code hides."""
    f = "edit core/authz_rules.go"
    for i in range(3):
        _decided_commit(repo, f"c{i}", f"Gitvow-Accepted: {f} by nikhil", f"2026-09-0{i + 1}T12:00:00")
    (repo / "staged.txt").write_text("mine\n")
    git(repo, "add", "staged.txt")
    monkeypatch.chdir(repo)
    assert cli.main(["rules", "accept", "--all"]) == 1
    assert "there are staged changes" in capsys.readouterr().err
    git(repo, "commit", "-qm", "my own work")
    assert cli.main(["rules", "accept", "--all"]) == 0
    capsys.readouterr()
    assert git(repo, "diff", "HEAD~1", "HEAD") == ""
