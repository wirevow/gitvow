"""The export bundle (0.20): the record leaves as an inspectable directory, never the session."""

import json
import os

from gitvow import claims as clm
from gitvow import cli
from gitvow import export as ex
from tests.conftest import git

CID = "clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6D"


def _decided_commit(repo, name, trailers, session="sess-1", note=None):
    (repo / f"{name}.txt").write_text(f"{name}\n")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-qm", f"{name}\n\nGitvow-Session: {session}\nGitvow-Step: 1\n{trailers}\n")
    if note is not None:
        git(repo, "notes", f"--ref=gitvow/{session}", "add", "-f", "-m", "gitvow-session\n" + json.dumps(note), "HEAD")


def _populate(repo, home, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    _decided_commit(
        repo,
        "c1",
        "Gitvow-Accepted: edit core/authz_rules.go by priya scope=staging: reviewed\nGitvow-Observed: edit Dockerfile",
        note={
            "schema": 7,
            "step": 1,
            "tool_calls_so_far": 12,
            "assistant_turns_so_far": 5,
            "files_written_by_agent_this_session": ["core/authz_rules.go"],
            "attribution": {
                "files_in_commit": 1,
                "touched_by_agent": 1,
                "lines_added_in_commit": 3,
                "agent_share": 1.0,
            },
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "estimated_cost_usd": 0.01},
            "edits_outside_repository": {"other-repo": {"count": 2, "paths": ["svc/main.go"]}},
            "last_stated_plan": "a plan with a secret ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 in it",
            "decisions": [
                {
                    "finding": "edit core/authz_rules.go",
                    "kind": "edit",
                    "path": "core/authz_rules.go",
                    "reason": "edits an authorization file",
                    "answer": "accepted",
                    "by": "priya",
                    "authority": "policy",
                    "scope": "staging",
                    "human_turns_after_card": 1,
                    "proposed": None,
                },
                {
                    "finding": "edit Dockerfile",
                    "kind": "edit",
                    "path": "Dockerfile",
                    "reason": "edits a container build",
                    "answer": "observed",
                },
            ],
        },
    )
    # a decision whose note carries a token: the row is refused at export, never rewritten
    _decided_commit(
        repo,
        "c2",
        "Gitvow-Declined: edit .github/workflows/ci.yml by dmitri: token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 leaked here",
        session="sess-2",
    )
    (repo / ".git" / "gitvow-hooks.log").write_text(
        "\n".join(
            json.dumps(e)
            for e in (
                {
                    "ts": "2026-09-16T10:00:00",
                    "kind": "confirm_required",
                    "reason": "pushing to a remote",
                    "session_id": "sess-1",
                    "detail": "git push origin main",
                },
                {
                    "ts": "2026-09-16T10:01:00",
                    "kind": "confirm_required",
                    "reason": "pushing to a remote",
                    "session_id": "sess-1",
                },
                {
                    "ts": "2026-09-16T10:02:00",
                    "kind": "blocked",
                    "reason": "force push",
                    "session_id": "sess-1",
                    "detail": "git push --force",
                },
                {"ts": "2026-09-16T10:03:00", "kind": "allowed", "tool": "Bash", "detail": "ls"},
            )
        )
        + "\n"
    )
    cands = tmp_path / "c.jsonl"
    cands.write_text(
        json.dumps(
            {
                "claim_id": CID,
                "text": "we don't do GET here",
                "speaker": "t",
                "kind": "system",
                "travels_with": "code",
                "paths": ["ingest/"],
                "confidence": 0.9,
            }
        )
        + "\n"
    )
    clm.import_candidates(str(repo), str(cands), str(home))
    clm.decide(str(repo), "1", "confirmed", {})


def test_bundle_carries_the_record_and_refuses_what_redaction_would_change(repo, home, tmp_path):
    _populate(repo, home, tmp_path)
    rules = []  # the built-in patterns alone; a GitHub token trips them
    b = ex.build(str(repo), since=None, rules=rules)
    m, a = b["manifest"], b["attestation"]
    assert m["kind"] == "record" and m["source"]["remote"] == "github.com/acme/payments-api"
    assert m["consent"]["decisions"] and not m["consent"]["sessions"] and not m["consent"]["gate_events"]
    assert {p["class"] for p in m["parts"]} == {"decisions", "observed", "claims", "rule_verdicts", "meter"}
    # the accepted decision travels with its note fields; the one carrying a token is refused, not rewritten
    d = b["parts"]["decisions"]
    assert len(d) == 1 and d[0]["answer"] == "accepted" and d[0]["scope"] == "staging" and d[0]["authority"] == "policy"
    assert d[0]["binding"] == "note" and d[0]["human_turns_after_card"] == 1
    assert len(a["refused"]) == 1 and a["refused"][0]["part"] == "decisions.jsonl"
    assert "ghp_" not in json.dumps(b["parts"])
    assert [o["finding"] for o in b["parts"]["observed"]] == ["edit Dockerfile"]
    c = b["parts"]["claims"]
    assert len(c) == 1 and c[0]["claim_id"] == CID and c[0]["reach"] == "repo" and c[0]["paths"] == ["ingest/"]
    assert b["parts"]["meter"]["floor"] is True and b["parts"]["meter"]["commits"] >= 3
    assert "transcripts" in a["never_carried"] and a["signature"] is None
    # canonical: the same repository yields the same digest twice
    assert ex.build(str(repo), since=None, rules=rules)["manifest"]["digest"] == m["digest"]


def test_consent_controls_files_and_sessions_never_carry_plans_or_paths(repo, home, tmp_path):
    _populate(repo, home, tmp_path)
    b = ex.build(str(repo), since=None, override=["sessions", "gate_events"], rules=[])
    assert set(b["parts"]) == {"sessions", "gate_events"}
    s = b["parts"]["sessions"]
    assert len(s) == 1 and s[0]["session_id"] == "sess-1" and s[0]["last_stated_plan"] is None
    assert s[0]["edits_outside_repository"] == {"other-repo": {"count": 2}}  # counts, never the paths
    assert s[0]["usage"]["total_tokens"] == 120 and s[0]["attribution"]["agent_share"] == 1.0
    g = b["parts"]["gate_events"]
    assert {(r["kind"], r["reason"], r["count"]) for r in g} == {
        ("confirm_required", "pushing to a remote", 2),
        ("blocked", "force push", 1),
    }
    assert "git push" not in json.dumps(g)  # counts and reasons, never a command
    # a committed consent file is honoured
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "export.json").write_text(json.dumps({"consent": {"meter": False, "sessions": True}}))
    b2 = ex.build(str(repo), since=None, rules=[])
    assert "meter" not in b2["parts"] and "sessions" in b2["parts"] and "decisions" in b2["parts"]
    try:
        ex.build(str(repo), since=None, override=["transcripts"])
        raise AssertionError
    except ValueError as e:
        assert "unknown export class" in str(e)


def test_cli_export_dry_run_writes_nothing_and_a_real_run_writes_the_directory(
    repo, home, tmp_path, monkeypatch, capsys
):
    _populate(repo, home, tmp_path)
    monkeypatch.chdir(repo)
    out = tmp_path / "bundle"
    assert cli.main(["export", "--dry-run", "--why", "--since", "1y", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    assert (
        "what leaves, one file per consented class" in text
        and "decisions.jsonl" in text
        and "refused by redaction" in text
    )
    assert "not consented, no file: sessions, gate_events" in text and not out.exists()
    assert cli.main(["export", "--since", "1y", "--out", str(out)]) == 0
    files = sorted(os.listdir(out))
    assert files == [
        "attestation.json",
        "claims.jsonl",
        "decisions.jsonl",
        "manifest.json",
        "meter.json",
        "observed.jsonl",
        "rule-verdicts.jsonl",
    ]
    manifest = json.loads((out / "manifest.json").read_text())
    for p in manifest["parts"]:
        import hashlib

        assert hashlib.sha256((out / p["path"]).read_bytes()).hexdigest() == p["sha256"]
    assert manifest["frontier"]["head"] == git(repo, "rev-parse", "HEAD")
    assert cli.main(["export", "--consent", "decisions,claims", "--out", str(tmp_path / "b2")]) == 0
    assert sorted(os.listdir(tmp_path / "b2")) == [
        "attestation.json",
        "claims.jsonl",
        "decisions.jsonl",
        "manifest.json",
    ]
