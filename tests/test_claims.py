"""Claims: a person's statement about their system, confirmed by them, kept in git (0.19)."""

import json
import subprocess

from gitvow import claims as clm
from gitvow import cli
from gitvow.decisions import parse_trailers
from gitvow.hooks import session_start
from tests.conftest import git


def _candidates(tmp_path, rows):
    p = tmp_path / "cands.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


CID1 = "clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6D"
CID2 = "clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6E"
CID3 = "clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6F"
ROWS = [
    {
        "claim_id": CID1,
        "text": "we don't do any GET calls here, only List, CopyObject and DeleteObject",
        "speaker": "t",
        "kind": "system",
        "travels_with": "code",
        "paths": ["ingest/s3/"],
        "confidence": 0.9,
        "why": "a standing constraint",
        "source": {"kind": "session", "file": "s1.jsonl", "message": 2},
        "when": "2026-08-14T05:12:50",
    },
    {
        "claim_id": CID2,
        "text": "the currency table is partitioned by month because vendor files arrive monthly",
        "speaker": "priya",
        "kind": "system",
        "travels_with": "code",
        "paths": [],
        "confidence": 0.7,
        "why": "a reason",
        "source": {"kind": "session", "file": "s2.jsonl", "message": 9},
    },
    {
        "claim_id": CID3,
        "text": "go to main and pull always when investigating",
        "speaker": "t",
        "kind": "preference",
        "travels_with": "person",
        "paths": [],
        "confidence": 0.8,
        "why": "a habit",
    },
    {"claim_id": "not-an-id", "text": "x", "speaker": "t"},
    {"claim_id": CID1, "text": "duplicate of the first", "speaker": "t"},
]


def test_import_queues_repo_claims_and_keeps_preferences_local(repo, home, tmp_path):
    counts = clm.import_candidates(str(repo), _candidates(tmp_path, ROWS), str(home))
    assert counts == {"queued": 2, "person": 1, "duplicate": 1, "answered": 0, "refused": 1}
    q = clm.queue(str(repo))
    assert [r["n"] for r in q] == [1, 2] and q[0]["claim_id"] == CID1  # most confident first
    prefs = clm.person_claims(str(home), "t")
    assert len(prefs) == 1 and prefs[0]["claim_id"] == CID3
    assert clm.person_claims(str(home), "someone-else") == []
    # the queue is local state, never in the worktree
    assert not (repo / "gitvow-claims.json").exists() and (repo / ".git" / "gitvow-claims.json").exists()
    text = clm.render_queue(q)
    assert "2 candidate claims waiting" in text and "paths: ingest/s3/" in text and "gitvow claims confirm" in text


def test_confirm_writes_trailer_note_and_leaves_the_queue(repo, home, tmp_path):
    clm.import_candidates(str(repo), _candidates(tmp_path, ROWS), str(home))
    _head, line, payload = clm.decide(str(repo), "1", "confirmed", {}, reason="still true")
    assert (
        line
        == f"Gitvow-Claim-Confirmed: {CID1} by t paths=ingest/s3/: we don't do any GET calls here, only List, CopyObject and DeleteObject"
    )
    body = git(repo, "log", "-1", "--format=%B")
    assert line in body and git(repo, "diff", "HEAD~1", "HEAD") == ""  # an empty commit
    assert payload["authority"] == "speaker" and payload["edited"] is False and payload["reach"] == "repo"
    note = git(repo, "notes", f"--ref={clm.CLAIMS_NOTES_REF}", "show", "HEAD")
    assert note.startswith("gitvow-claim") and json.loads(note.split("\n", 1)[1])["source"] == {
        "kind": "session",
        "file": "s1.jsonl",
        "message": 2,
    }
    assert [r["claim_id"] for r in clm.queue(str(repo))] == [CID2]
    # an older gitvow sees no decision trailer at all on this commit: the grammar grew by a new name
    assert parse_trailers(body) == []
    c = clm.confirmed(str(repo))
    assert len(c) == 1 and c[0]["claim_id"] == CID1 and c[0]["paths"] == ["ingest/s3/"] and c[0]["note"] is True
    # confirming someone else's claim records the confirmer's authority, not "speaker"
    _head2, line2, payload2 = clm.decide(
        str(repo),
        CID2,
        "confirmed",
        {},
        edit="the currency table is partitioned by month; vendor files arrive monthly",
        paths=["etl/currency/"],
    )
    assert payload2["authority"] == "commit-access" and payload2["edited"] is True
    assert payload2["original_text"].startswith("the currency table is partitioned by month because")
    assert "paths=etl/currency/" in line2
    assert clm.queue(str(repo)) == []
    block = clm.render(clm.confirmed(str(repo)))
    assert block.startswith(clm.START) and block.rstrip().endswith(clm.END)
    assert "- t (ingest/s3/): \"we don't do any GET calls here" in block and "confirmed on" in block
    assert "- priya (etl/currency/):" in block and "confirmed by t on" in block
    assert "do not permit anything" in block


def test_reject_withdraws_and_reimport_skips_answered(repo, home, tmp_path):
    p = _candidates(tmp_path, ROWS)
    clm.import_candidates(str(repo), p, str(home))
    clm.decide(str(repo), "1", "confirmed", {})
    _head, line, payload = clm.decide(str(repo), CID2, "rejected", {}, reason="not true any more")
    assert line == f"Gitvow-Claim-Rejected: {CID2} by t: not true any more" and payload["verdict"] == "rejected"
    assert [r["claim_id"] for r in clm.confirmed(str(repo))] == [CID1]
    counts = clm.import_candidates(str(repo), p, str(home))
    assert counts["answered"] == 3 and counts["queued"] == 0  # both CID1 rows and CID2
    # a later rejection of a confirmed claim withdraws it from the rendered block
    (repo / ".git" / "gitvow-claims.json").write_text(json.dumps([{**ROWS[0], "claim_id": CID1}]))
    clm.decide(str(repo), CID1, "rejected", {}, reason="the service does GET now")
    assert clm.confirmed(str(repo)) == [] and clm.render(clm.confirmed(str(repo))) == ""


def test_staged_work_is_refused_and_bad_picks_are_named(repo, home, tmp_path):
    clm.import_candidates(str(repo), _candidates(tmp_path, ROWS), str(home))
    (repo / "a.txt").write_text("staged\n")
    git(repo, "add", "a.txt")
    try:
        clm.decide(str(repo), "1", "confirmed", {})
        raise AssertionError("staged work must be refused")
    except ValueError as e:
        assert "staged changes" in str(e)
    git(repo, "reset", "-q")
    for which, msg in (("9", "no claim number 9"), ("clm_nope", "no queued claim")):
        try:
            clm.decide(str(repo), which, "confirmed", {})
            raise AssertionError
        except ValueError as e:
            assert msg in str(e)


def test_cli_claims_end_to_end_and_session_start_context(repo, home, tmp_path, payload, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["claims"]) == 0 and "No candidate claims waiting" in capsys.readouterr().out
    assert cli.main(["claims", "import", _candidates(tmp_path, ROWS)]) == 0
    out = capsys.readouterr().out
    assert "queued 2" in out and "person 1" in out and "refused 1" in out
    assert cli.main(["claims"]) == 0 and "1. [system 0.90] t:" in capsys.readouterr().out
    assert cli.main(["claims", "confirm", "1", "--reason", "checked"]) == 0
    out, err = capsys.readouterr()
    assert out.strip().startswith(f"Gitvow-Claim-Confirmed: {CID1} by t") and "empty commit" in err
    assert cli.main(["claims", "show", CID1]) == 0 and "we don't do any GET calls" in capsys.readouterr().out
    assert cli.main(["claims", "reject", "1", "--reason", "no"]) == 0
    assert capsys.readouterr().out.strip().startswith(f"Gitvow-Claim-Rejected: {CID2} by t: no")
    # rendered into the agent's instruction file under its own managed section, idempotently
    assert cli.main(["claims", "--write"]) == 0
    assert "added managed claims section in" in capsys.readouterr().out
    text = (repo / "CLAUDE.md").read_text()
    assert text.count(clm.START) == 1 and "we don't do any GET calls" in text
    assert (
        cli.main(["claims", "--write"]) == 0
        and (repo / "CLAUDE.md").read_text() == text.replace("updated", "updated")
        and text.count(clm.START) == 1
    )
    # session start hands the confirmed claims and the person's own preferences to the agent
    code, ctx = session_start(payload("SessionStart"), str(home))
    assert code == 0 and "Confirmed by the people who own this code" in ctx and "go to main and pull always" in ctx
    assert "priya" not in ctx  # the rejected claim is not context
    # gitvow show finds the claims note on the verdict commit
    assert cli.main(["show", "HEAD~1"]) == 0 and "gitvow-claim" in capsys.readouterr().out


def test_hooks_disabled_commit_does_not_fire_the_card(repo):
    """A claim verdict is written with hooks disabled, like a rule verdict: it carries nothing but the verdict."""
    r = subprocess.run(["git", "log", "--oneline", "-1"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0


def test_paths_are_suggested_from_the_tree_and_accepted_with_suggested(repo, home, tmp_path):
    for p in ("ingest/s3/copier.py", "ingest/s3/lister.py", "etl/currency/partition.py", "docs/readme.md"):
        (repo / p).parent.mkdir(parents=True, exist_ok=True)
        (repo / p).write_text("x\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "tree")
    clm.import_candidates(str(repo), _candidates(tmp_path, ROWS), str(home))
    q = clm.queue(str(repo))
    assert q[0]["paths"] == ["ingest/s3/"] and "suggested" not in q[0]  # already bound, nothing to suggest
    assert q[1]["suggested"] == ["etl/currency/"]  # "currency" in the claim matches the directory
    assert "suggested: etl/currency/" in clm.render_queue(q)
    assert clm.suggest_paths("nothing in common with any file", clm.repo_tree(str(repo))) == []
    assert clm.suggest_paths("the s3 copier lists objects", clm.repo_tree(str(repo)))[0] == "ingest/s3/"
    _h, line, payload = clm.decide(str(repo), CID2, "confirmed", {}, paths=["suggested"])
    assert payload["paths"] == ["etl/currency/"] and "paths=etl/currency/" in line
