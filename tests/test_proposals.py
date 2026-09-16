"""Proposed policy rules (0.21): what the loop found the gate letting through, accepted as a reviewed commit."""

import json

from gitvow import cli
from gitvow import proposals as pr
from gitvow.decisions import parse_trailers
from gitvow.policy import load_policy
from tests.conftest import git

LOOP_JSON = {
    "engineer_weeks": 25.0,
    "card": [
        {
            "kind": "edit",
            "id": "dockerfile",
            "reason": "edits a container build",
            "weight": 4,
            "events": 85,
            "sessions": 12,
            "repos": 9,
            "engineers": 3,
            "shapes": ["./", "scanner/", "api/"],
            "tier": "card",
            "asks_card": 27,
            "asks_per_engineer_week_card": 1.1,
            "events_per_engineer_week": 3.4,
            "rule": {
                "id": "dockerfile",
                "pattern": r"(^|/)Dockerfile[^/]*$",
                "reason": "edits a container build",
                "when": "commit",
            },
        },
        {
            "kind": "run",
            "id": "aws-write",
            "reason": "AWS write outside the repository",
            "weight": 4,
            "events": 106,
            "sessions": 20,
            "repos": 11,
            "engineers": 3,
            "shapes": ["s3 cp", "ecr create-repository"],
            "reads_left_alone": 1308,
            "tier": "card",
            "asks_card": 33,
            "asks_per_engineer_week_card": 1.34,
            "events_per_engineer_week": 4.3,
            "rule": {
                "id": "aws-write",
                "program": "aws",
                "verbs": [r"s3\s+(cp|sync|mv|rm|rb|mb)"],
                "reason": "AWS write outside the repository",
                "when": "commit",
            },
        },
    ],
    "observe": [
        {
            "kind": "edit",
            "id": "dependencies",
            "reason": "edits dependencies",
            "weight": 2,
            "events": 136,
            "repos": 13,
            "engineers": 3,
            "tier": "observe",
            "events_per_engineer_week": 5.5,
            "rule": {
                "id": "dependencies",
                "pattern": r"(^|/)(pom\.xml|package\.json)$",
                "reason": "edits dependencies",
                "when": "observe",
            },
        },
        {"kind": "run", "id": "broken", "reason": "no rule body"},
        {"kind": "run", "id": "badregex", "rule": {"id": "badregex", "pattern": "(", "reason": "unbalanced"}},
    ],
}


def _file(tmp_path, data=LOOP_JSON):
    p = tmp_path / "proposal.json"
    p.write_text(json.dumps(data))
    return str(p)


def test_import_queues_by_consequence_and_refuses_broken_rules(repo, home, tmp_path):
    counts = pr.import_proposals(str(repo), _file(tmp_path))
    assert counts == {"queued": 3, "duplicate": 0, "answered": 0, "refused": 2}
    q = pr.queue(str(repo))
    assert [r["id"] for r in q] == ["aws-write", "dockerfile", "dependencies"]  # weight, then events
    assert q[0]["kind"] == "run" and q[0]["tier"] == "commit" and q[2]["tier"] == "observe"
    assert q[0]["evidence"]["reads_left_alone"] == 1308 and q[1]["evidence"]["shapes"] == ["./", "scanner/", "api/"]
    text = pr.render_queue(q)
    assert "1. [commit] aws-write" in text and "program aws verbs" in text and "1.34 asks/eng/wk" in text
    assert "3. [observe] dependencies" in text and "5.5 events/eng/wk recorded, 0 asks" in text
    assert pr.import_proposals(str(repo), _file(tmp_path))["duplicate"] == 3
    # a bare policy fragment imports too
    frag = {
        "path_confirm": [
            {
                "id": "helm-values",
                "pattern": r"(^|/)values[^/]*\.ya?ml$",
                "reason": "edits Helm values",
                "when": "commit",
            }
        ]
    }
    assert pr.import_proposals(str(repo), _file(tmp_path, frag))["queued"] == 1
    assert pr.queue(str(repo))[-1]["kind"] == "edit"


def test_accept_writes_the_rule_as_a_reviewed_commit_and_the_gate_uses_it(repo, home, tmp_path):
    pr.import_proposals(str(repo), _file(tmp_path))
    assert not (repo / ".gitvow" / "policy.json").exists()
    _head, line = pr.decide(str(repo), "1", "accepted", {}, reason="we push to S3 from CI only")
    assert line == "Gitvow-Policy-Accepted: aws-write by t when=commit: we push to S3 from CI only"
    body = git(repo, "log", "-1", "--format=%B")
    assert line in body and "creates it from the shipped default" in body and "events 106" in body
    assert (
        git(repo, "diff", "--name-only", "HEAD~1", "HEAD") == ".gitvow/policy.json"
    )  # the commit carries the policy alone
    assert parse_trailers(body) == []  # an older gitvow sees no decision trailer
    pol = load_policy(str(repo), str(home))
    rule = next(r for r in pol["bash_confirm"] if r.get("id") == "aws-write")
    assert rule["program"] == "aws" and rule["when"] == "commit"
    assert len(pol["bash_deny"]) >= 8  # the shipped default came along
    # the gate now raises the accepted rule
    from gitvow.policy import evaluate

    d = evaluate(pol, "Bash", {"command": "aws --profile p s3 cp a s3://b/"})
    assert d.deferred and d.findings[0]["finding"] == "run aws (AWS write outside the repository)"
    # accepting into observe overrides the proposed tier; the second rule appends to the same file
    _head2, line2 = pr.decide(str(repo), "dockerfile", "accepted", {}, when="observe")
    assert "when=observe" in line2
    pol = load_policy(str(repo), str(home))
    assert next(r for r in pol["path_confirm"] if r.get("id") == "dockerfile")["when"] == "observe"
    assert [r["id"] for r in pr.queue(str(repo))] == ["dependencies"]
    # a second acceptance of the same id is refused
    pr.import_proposals(str(repo), _file(tmp_path))  # aws-write and dockerfile are answered, not re-queued
    assert [r["id"] for r in pr.queue(str(repo))] == ["dependencies"]


def test_reject_is_an_empty_commit_and_stops_reproposal(repo, home, tmp_path):
    pr.import_proposals(str(repo), _file(tmp_path))
    _head, line = pr.decide(str(repo), "dependencies", "rejected", {}, reason="too noisy")
    assert line == "Gitvow-Policy-Rejected: dependencies by t: too noisy" and git(repo, "diff", "HEAD~1", "HEAD") == ""
    v = pr.verdicts(str(repo))
    assert v[0]["verdict"] == "rejected" and v[0]["id"] == "dependencies" and v[0]["note"] == "too noisy"
    counts = pr.import_proposals(str(repo), _file(tmp_path))
    assert counts["answered"] == 1 and counts["duplicate"] == 2


def test_authority_and_staged_work(repo, home, tmp_path):
    pr.import_proposals(str(repo), _file(tmp_path))
    try:
        pr.decide(str(repo), "1", "accepted", {"decisions": {"authorities": ["nikhil"]}})
        raise AssertionError
    except ValueError as e:
        assert "not named under decisions.authorities" in str(e)
    (repo / "a.txt").write_text("staged\n")
    git(repo, "add", "a.txt")
    try:
        pr.decide(str(repo), "1", "accepted", {})
        raise AssertionError
    except ValueError as e:
        assert "staged changes" in str(e)
    git(repo, "reset", "-q")
    try:
        pr.decide(str(repo), "1", "accepted", {}, when="sometimes")
        raise AssertionError
    except ValueError as e:
        assert "when must be one of" in str(e)


def test_cli_policy_end_to_end(repo, home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["policy"]) == 0 and "No proposed policy rules" in capsys.readouterr().out
    assert cli.main(["policy", "import", _file(tmp_path)]) == 0
    assert "queued 3" in capsys.readouterr().out
    assert cli.main(["policy"]) == 0 and "1. [commit] aws-write" in capsys.readouterr().out
    assert cli.main(["policy", "accept", "1", "--reason", "ok"]) == 0
    out, err = capsys.readouterr()
    assert out.strip().startswith("Gitvow-Policy-Accepted: aws-write by t when=commit") and ".gitvow/policy.json" in err
    assert cli.main(["policy", "reject", "dependencies"]) == 0
    assert capsys.readouterr().out.strip().startswith("Gitvow-Policy-Rejected: dependencies by t")
    assert cli.main(["policy", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in data["queue"]] == ["dockerfile"] and len(data["verdicts"]) == 2
