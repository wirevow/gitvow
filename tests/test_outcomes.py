"""0.31: outcomes. Did the decision hold? Graded from the pull request verdict, the landing and reverts."""

import json

import pytest

from gitvow import cli
from gitvow import outcomes as oc
from gitvow.digest import build, render
from gitvow.policy import DEFAULT_POLICY_PATH, PolicyError, load_policy
from gitvow.serve import call_tool, tool_list
from tests.conftest import git

ACCEPT = "Gitvow-Session: s1\nGitvow-Step: 1\nGitvow-Accepted: edit core/authz_rules.go by t scope=staging: reviewed"
DECLINE = "Gitvow-Session: s1\nGitvow-Step: 2\nGitvow-Declined: edit .github/workflows/ci.yml by t"
REFER = "Gitvow-Session: s1\nGitvow-Step: 3\nGitvow-Referred: edit deploy/values.yaml by t to=platform"


def _commit(repo, name, trailers):
    (repo / name).write_text(name + "\n")
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"{name}\n\n{trailers}")
    return git(repo, "rev-parse", "HEAD")


def _branch(repo):
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def test_grade_matrix():
    assert oc.grade("accepted", "merged", False) == "held"
    assert oc.grade("accepted", "direct", False) == "held"
    assert oc.grade("accepted", "merged", True) == "not_held"
    assert oc.grade("accepted", "closed", False) == "not_held"
    assert oc.grade("declined", "closed", False) == "held"
    assert oc.grade("declined", "merged", False) == "overridden"
    assert oc.grade("declined", "direct", True) == "held"
    for answer in ("accepted", "declined", "referred"):
        assert oc.grade(answer, "open", False) == "pending"
        assert oc.grade(answer, "unknown", False) == "pending"
    assert oc.grade("referred", "merged", False) == "pending"
    # rework: weaker than a revert, its own word, only for an accepted change that landed and stayed
    assert oc.grade("accepted", "merged", False, True) == "reworked"
    assert oc.grade("accepted", "direct", False, True) == "reworked"
    assert oc.grade("accepted", "merged", True, True) == "not_held"  # a revert outranks rework
    assert oc.grade("declined", "merged", False, True) == "overridden"
    assert oc.grade("accepted", "open", False, True) == "pending"


def test_git_alone_grades_landed_reverted_and_referred(repo, home):
    # the fixture's branch is a production branch in the default policy (main or master)
    assert _branch(repo) in oc.DEFAULT_PRODUCTION
    a = _commit(repo, "a.go", ACCEPT)
    d = _commit(repo, "ci.yml", DECLINE)
    r = _commit(repo, "values.yaml", REFER)
    b = _commit(repo, "b.go", ACCEPT.replace("Step: 1", "Step: 4"))
    git(repo, "revert", "--no-edit", b)
    out = oc.run(str(repo), {}, since=None, scm=False)
    by = {row["sha"]: row for row in out["rows"]}
    assert out["scm"] == "off" and out["commits"] == 4 and out["written"] == 4
    assert by[a[:7]]["verdict"] == {
        "kind": "direct",
        "source": "git",
        "pull_request": None,
        "url": None,
        "landed_ts": by[a[:7]]["verdict"]["landed_ts"],
        "landed_at": by[a[:7]]["verdict"]["landed_at"],
    }
    assert by[a[:7]]["decisions"][0]["outcome"] == "held"
    assert by[d[:7]]["decisions"][0]["outcome"] == "overridden"  # declined, landed anyway
    assert by[r[:7]]["decisions"][0]["outcome"] == "pending"  # a referral is not an answer
    rv = by[b[:7]]["revert"]
    assert rv["in_window"] is True and rv["after_seconds"] < 60 and by[b[:7]]["decisions"][0]["outcome"] == "not_held"
    assert out["outcomes"] == {"held": 1, "not_held": 1, "reworked": 0, "overridden": 1, "pending": 1}
    assert out["verdicts"]["direct"] == 4
    # written beside the decision, on its own ref, never touching the session note
    note = oc.read(str(repo), a)
    assert note["schema"] == 2 and note["decisions"][0]["answer"] == "accepted"
    assert git(repo, "notes", "--ref=gitvow/outcomes", "show", a).startswith("gitvow-outcome")
    assert git(repo, "notes", "--ref=gitvow/s1", "list") == ""
    text = oc.render(out)
    assert (
        "4 decisions graded on 4 commits · 1 held · 1 did not hold · 0 reworked · 1 overridden · 1 pending · revert window 1d"
        in text
    )
    assert "4 landed without a pull request" in text and "forge not asked" in text
    assert f"reverted {git(repo, 'rev-parse', 'HEAD')[:7]} after 0m" in text
    # idempotent, and a final grade is kept rather than recomputed once the window has passed
    again = oc.run(str(repo), {}, since=None, scm=False, now=int(__import__("time").time()) + 3 * 86400)
    assert again["reused"] == 4 and again["written"] == 0 and again["outcomes"] == out["outcomes"]
    forced = oc.run(str(repo), {}, since=None, scm=False, regrade=True)
    assert forced["written"] == 4


def test_forge_verdicts_merged_closed_open_and_fallback(repo, home):
    git(repo, "remote", "add", "origin", "git@github.com:acme/ledger.git")
    a = _commit(repo, "a.go", ACCEPT)
    d = _commit(repo, "ci.yml", DECLINE)
    o = _commit(repo, "o.go", ACCEPT.replace("Step: 1", "Step: 5"))
    calls = []

    def gh(args):
        calls.append(args[0])
        sha = args[0].rsplit("/", 2)[-2]
        if sha == a:
            return [
                {
                    "number": 7,
                    "state": "closed",
                    "merged_at": "2026-09-20T10:00:00Z",
                    "html_url": "u7",
                    "created_at": "2026-09-19",
                }
            ]
        if sha == d:
            return [
                {
                    "number": 8,
                    "state": "closed",
                    "merged_at": None,
                    "closed_at": "2026-09-21T10:00:00Z",
                    "created_at": "2026-09-20",
                }
            ]
        return [{"number": 9, "state": "open", "merged_at": None, "created_at": "2026-09-22"}]

    out = oc.run(str(repo), {}, since=None, gh=gh)
    by = {row["sha"]: row for row in out["rows"]}
    assert out["scm"] == "ok" and calls[0] == f"repos/acme/ledger/commits/{o}/pulls"
    assert by[a[:7]]["verdict"]["kind"] == "merged" and by[a[:7]]["verdict"]["pull_request"] == 7
    assert by[a[:7]]["verdict"]["landed_at"] == "2026-09-20" and by[a[:7]]["decisions"][0]["outcome"] == "held"
    assert (
        by[d[:7]]["verdict"]["kind"] == "closed" and by[d[:7]]["decisions"][0]["outcome"] == "held"
    )  # declined, not merged
    assert by[o[:7]]["verdict"]["kind"] == "open" and by[o[:7]]["decisions"][0]["outcome"] == "pending"

    # the forge failing falls back to git for the rest of the run, and the note says which source spoke
    def broken(args):
        raise oc.ScmUnavailableError("gh: HTTP 401")

    out2 = oc.run(str(repo), {}, since=None, gh=broken, regrade=True)
    assert out2["scm"] == "off" and out2["scm_reason"] == "gh: HTTP 401"
    assert {row["verdict"]["source"] for row in out2["rows"]} == {"git"}
    # not on github: git alone, said so
    git(repo, "remote", "set-url", "origin", "https://gitlab.example.com/acme/ledger.git")
    out3 = oc.run(str(repo), {}, since=None, gh=gh, regrade=True)
    assert out3["scm_reason"] == "origin is not on github.com"


def test_pulls_for_prefers_the_merged_pull_request(repo):
    git(repo, "remote", "add", "origin", "https://github.com/acme/ledger")
    rows = [
        {"number": 1, "state": "closed", "merged_at": None, "created_at": "2026-09-01"},
        {
            "number": 2,
            "state": "closed",
            "merged_at": "2026-09-02T00:00:00Z",
            "created_at": "2026-09-02",
            "html_url": "u",
        },
    ]
    p = oc.pulls_for(str(repo), "abc", lambda args: rows)
    assert p["number"] == 2 and p["url"] == "u"
    assert oc.pulls_for(str(repo), "abc", lambda args: []) is None
    assert oc.github_repo(str(repo)) == ("acme", "ledger")


def test_digest_report_show_and_server_carry_the_grade(repo, home, monkeypatch, capsys):
    a = _commit(repo, "a.go", ACCEPT)
    monkeypatch.chdir(repo)
    assert cli.main(["outcomes", "--no-scm", "--dry-run"]) == 0
    assert "dry run: nothing written" in capsys.readouterr().out
    assert oc.read(str(repo), a) is None
    assert cli.main(["outcomes", "--no-scm", "--json"]) == 0
    j = json.loads(capsys.readouterr().out)
    assert j["written"] == 1 and j["rows"][0]["decisions"][0]["outcome"] == "held"
    d = build(str(repo), "7d")
    assert d["outcomes"]["decisions"] == 1 and d["outcomes"]["outcomes"]["held"] == 1
    assert (
        "Outcomes: 1 decision graded on 1 commit · 1 held · 0 did not hold · 0 reworked · 0 overridden · 0 pending · revert window 1d · 1 landed without a pull request"
        in render(d)
    )
    assert " by t" not in render(d).split("Outcomes:")[1].split("\n")[0]
    assert cli.main(["show", a]) == 0
    out = capsys.readouterr().out
    assert "gitvow-outcome" in out and "(no session note)" in out
    from gitvow.report import build as report_build
    from gitvow.report import render_markdown

    rep = report_build(str(repo), "HEAD~1", "HEAD", None)
    assert rep["commits"][0]["outcome"]["decisions"][0]["outcome"] == "held"
    assert "**Outcome:** direct on " in render_markdown(
        rep
    ) and "accepted edit core/authz_rules.go: held" in render_markdown(rep)
    assert "record_outcomes" in {t["name"] for t in tool_list()}
    r = call_tool("record_outcomes", {}, str(repo), str(home))
    assert r["data"]["summary"]["outcomes"]["held"] == 1 and "→ held" in r["text"]


def test_policy_window_and_validation(repo, home):
    with open(DEFAULT_POLICY_PATH) as fh:
        d = json.load(fh)
    assert oc.settings(d)["revert_window_days"] == 1 and oc.settings({})["production_branches"] == oc.DEFAULT_PRODUCTION
    (repo / ".gitvow").mkdir(exist_ok=True)
    d["outcomes"] = {"revert_window_days": 7}
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    assert oc.settings(load_policy(str(repo), str(home)))["revert_window_days"] == 7
    for bad in (0, 366, True, "1"):
        d["outcomes"] = {"revert_window_days": bad}
        (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
        with pytest.raises(PolicyError, match="revert_window_days"):
            load_policy(str(repo), str(home))
    d["outcomes"] = 3
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    with pytest.raises(PolicyError, match="outcomes must be an object"):
        load_policy(str(repo), str(home))


def test_revert_after_the_window_keeps_the_grade(repo, home):
    b = _commit(repo, "b.go", ACCEPT)
    git(repo, "revert", "--no-edit", b)
    # the revert is seconds later in wall time; a negative window cannot be set, so move "now" and the landing instead
    table = oc.reverts(str(repo))
    rv = oc.revert_of(b, table)
    assert rv is not None and rv["sha"] == git(repo, "rev-parse", "HEAD")
    # a revert 3 days after landing, under a 1-day window: recorded, grade unchanged
    from unittest import mock

    with mock.patch.object(oc, "reverts", return_value={b: {"sha": rv["sha"], "ts": rv["ts"] + 3 * 86400}}):
        out = oc.run(str(repo), {}, since=None, scm=False)
    row = out["rows"][0]
    assert row["revert"]["in_window"] is False and row["decisions"][0]["outcome"] == "held"
    assert "(after the window)" in oc.render(out)


def test_rework_on_the_same_lines_by_someone_else_within_the_window(repo, home):
    """An accepted change whose added lines another person rewrites days later is reworked; the author's own
    follow-up, or a rewrite of other lines, is not."""
    (repo / "svc.py").write_text("a = 1\nb = 2\nc = 3\n")
    git(repo, "add", "svc.py")
    git(repo, "commit", "-qm", "base")
    (repo / "svc.py").write_text("a = 1\nb = 20\nc = 3\n")  # the decision changes line 2
    git(repo, "commit", "-qam", f"tune b\n\n{ACCEPT}")
    decided = git(repo, "rev-parse", "HEAD")
    assert oc.added_ranges(str(repo), decided) == {"svc.py": [(2, 2)]}
    # the author's own follow-up on the same line: not rework
    (repo / "svc.py").write_text("a = 1\nb = 21\nc = 3\n")
    git(repo, "commit", "-qam", "nudge b")
    # someone else rewrites a different line: not rework
    (repo / "svc.py").write_text("a = 10\nb = 21\nc = 3\n")
    git(repo, "-c", "user.email=other@test", "-c", "user.name=other", "commit", "-qam", "touch a")
    out = oc.run(str(repo), {}, since=None, scm=False)
    assert out["rows"][0]["decisions"][0]["outcome"] == "held" and out["rows"][0]["rework"] is None
    # someone else rewrites the decided line within the window: reworked
    (repo / "svc.py").write_text("a = 10\nb = 2\nc = 3\n")
    git(repo, "-c", "user.email=other@test", "-c", "user.name=other", "commit", "-qam", "put b back by hand")
    other = git(repo, "rev-parse", "HEAD")
    out = oc.run(str(repo), {}, since=None, scm=False, regrade=True)
    row = out["rows"][0]
    assert row["decisions"][0]["outcome"] == "reworked"
    assert (
        row["rework"]["sha"] == other[:12] and row["rework"]["path"] == "svc.py" and row["rework"]["by_other"] is True
    )
    assert row["rework_window_days"] == 7 and row["schema"] == 2
    assert out["outcomes"]["reworked"] == 1
    text = oc.render(out)
    assert f"reworked by {other[:7]} after" in text and "1 reworked" in text
    # a revert outranks rework: covered by the grade matrix above; a real revert here would conflict with the rework
    # the window is policy
    from gitvow.policy import load_policy

    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"outcomes": {"rework_window_days": 400}}))
    with pytest.raises(PolicyError, match="rework_window_days"):
        load_policy(str(repo), str(home))
    assert oc.settings({"outcomes": {"rework_window_days": 3}})["rework_window_days"] == 3
