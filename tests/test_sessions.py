"""`gitvow sessions export` (0.36): the session itself, complete and unredacted, as a `sessions-raw` bundle for a
store the customer runs. Explicit, never carried by `gitvow sync`, never into a git sink, no outbox."""

import json
import os
import subprocess

from gitvow import cli
from gitvow import sessions as se
from gitvow import sync as sy
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git

TOKEN = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
TRANSCRIPT = [
    {"type": "user", "message": {"role": "user", "content": "rotate the key for ops@example.com"}},
    {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": f"using {TOKEN} for now"},
                {"type": "tool_use", "name": "Edit", "input": {"file_path": "calc.py"}},
            ],
        },
    },
]


def _session(repo, home, payload, transcript_at=None):
    """Drive one agent session through the hooks: a snapshot, a commit with a note, a ledger entry."""
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    tp = transcript_at or (home / ".claude" / "projects" / "-x" / "sess-1.jsonl")
    tp.parent.mkdir(parents=True, exist_ok=True)
    tp.write_text("".join(json.dumps(r) + "\n" for r in TRANSCRIPT))
    session_start(payload("SessionStart", transcript=str(tp)), str(home))
    (repo / "calc.py").write_text("v1\n")
    post_tool_use(payload("PostToolUse", "Edit", {"file_path": str(repo / "calc.py")}, transcript=str(tp)), str(home))
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript=str(tp)), str(home))
    git(repo, "add", "calc.py")
    git(repo, "commit", "-qm", "agent commit")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript=str(tp)), str(home))
    return tp


def test_open_session_then_ledger_entry_both_found_and_bundle_is_complete_and_unredacted(repo, home, payload):
    tp = _session(repo, home, payload)
    # still open: no ledger entry yet, the state names the transcript
    found = se.sessions_for(str(repo), str(home))
    assert (
        [s["session_id"] for s in found] == ["sess-1"] and found[0]["open"] and found[0]["transcript_path"] == str(tp)
    )
    # the session ends: the ledger entry now carries the transcript path (0.36)
    stop(payload("Stop", transcript=str(tp)), str(home))
    led = json.loads((home / ".gitvow" / "ledger" / "sess-1.json").read_text())
    assert led["transcript_path"] == str(tp) and led["repo"] == str(repo)
    found = se.sessions_for(str(repo), str(home))
    assert len(found) == 1 and not found[0]["open"]

    b = se.build(str(repo), str(home), since=None)
    m, a = b["manifest"], b["attestation"]
    assert m["kind"] == "sessions-raw" and m["source"]["remote"] == "github.com/acme/payments-api"
    names = sorted(b["parts"])
    assert names == ["sess-1.ledger.json", "sess-1.notes.json", "sess-1.snapshot-001.patch", "sess-1.transcript.jsonl"]
    # verbatim, unredacted: the token and the address are in the bytes, and the attestation says so
    assert TOKEN in b["parts"]["sess-1.transcript.jsonl"].decode()
    assert b["parts"]["sess-1.transcript.jsonl"] == tp.read_bytes()
    assert a["unredacted"] is True and a["complete"] is True and a["missing"] == []
    assert "gitvow sync" in a["never_by"] and "customer runs" in a["accepted_only_by"]
    assert "+v1" in b["parts"]["sess-1.snapshot-001.patch"].decode()
    notes = json.loads(b["parts"]["sess-1.notes.json"])
    assert len(notes) == 1 and notes[0]["note"]["schema"] >= 9
    s = m["sessions"][0]
    assert s["transcript"] == "included" and s["snapshots"] == 1 and s["notes"] == 1 and not s["open"]
    # the digest is over the parts; every part is hashed and sized
    assert all(p["sha256"] and p["bytes"] > 0 and p["session_id"] == "sess-1" for p in m["parts"])
    assert m["digest"].startswith("sha256:") and m["idempotency_key"] == m["digest"]
    why = se.render_why(b)
    assert "COMPLETE AND UNREDACTED" in why and "transcript included" in why and "1 snapshot(s)" in why


def test_missing_transcript_is_named_not_guessed_and_filters_apply(repo, home, payload):
    tp = _session(repo, home, payload)
    stop(payload("Stop", transcript=str(tp)), str(home))
    led = home / ".gitvow" / "ledger"
    # a second session of this repository whose transcript is gone, and a third of another repository
    (led / "sess-2.json").write_text(
        json.dumps(
            {
                "session_id": "sess-2",
                "repo": str(repo),
                "started": "2020-01-01T10:00:00",
                "ended": "2020-01-01T11:00:00",
            }
        )
    )
    (led / "sess-3.json").write_text(
        json.dumps({"session_id": "sess-3", "repo": "/elsewhere", "started": "2026-01-01T10:00:00"})
    )
    b = se.build(str(repo), str(home), since=None)
    assert [s["session_id"] for s in b["manifest"]["sessions"]] == ["sess-2", "sess-1"]
    assert b["attestation"]["complete"] is False
    assert b["attestation"]["missing"] == [
        {"session_id": "sess-2", "class": "transcript", "reason": "not found on this machine"}
    ]
    assert "missing on this machine: 1 transcript" in se.render_why(b)
    # the window leaves the 2020 session out; --session narrows by prefix; an unknown one is an error
    assert [s["session_id"] for s in se.build(str(repo), str(home), since="90d")["manifest"]["sessions"]] == ["sess-1"]
    assert [
        s["session_id"] for s in se.build(str(repo), str(home), since=None, only="sess-2")["manifest"]["sessions"]
    ] == ["sess-2"]
    try:
        se.build(str(repo), str(home), since=None, only="nope")
        raise AssertionError
    except ValueError as e:
        assert "no session matching" in str(e)
    # a transcript found by the agent's own layout when the ledger has no path
    (home / ".claude" / "projects" / "-y").mkdir(parents=True)
    (home / ".claude" / "projects" / "-y" / "sess-2.jsonl").write_text("{}\n")
    b = se.build(str(repo), str(home), since=None, only="sess-2", with_snapshots=False)
    assert b["attestation"]["complete"] and "sess-2.transcript.jsonl" in b["parts"]


def test_delivery_dir_ok_git_refused_sync_never_carries_it(repo, home, payload, tmp_path):
    tp = _session(repo, home, payload)
    stop(payload("Stop", transcript=str(tp)), str(home))
    b = se.build(str(repo), str(home), since=None)
    out = tmp_path / "bundle"
    written = se.write(b, str(out))
    assert sorted(os.path.basename(p) for p in written) == sorted([*b["parts"], "manifest.json", "attestation.json"])
    digest = b["manifest"]["digest"].split(":")[-1]
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    r = se.deliver({"name": "drop", "type": "dir", "path": str(prefix)}, str(out))
    assert r["status"] == "delivered" and r["path"].endswith(f"github.com_acme_payments-api/sessions/{digest}")
    assert (prefix / "github.com_acme_payments-api" / "sessions" / digest / "sess-1.transcript.jsonl").exists()
    assert se.deliver({"name": "drop", "type": "dir", "path": str(prefix)}, str(out))["status"] == "already held"
    r = se.deliver({"name": "team", "type": "git", "path": str(tmp_path / "store")}, str(out))
    assert r["status"] == "refused" and "git sink" in r["reason"]
    assert (
        se.deliver({"name": "gone", "type": "dir", "path": str(tmp_path / "nope")}, str(out))["status"] == "unreachable"
    )
    # the collector refuses to move it, whatever the sink
    try:
        sy.deliver({"name": "drop", "type": "dir", "path": str(prefix)}, str(out))
        raise AssertionError
    except ValueError as e:
        assert "carries 'record' only" in str(e)
    # and the record bundle is not a sessions bundle
    from gitvow import export as ex

    rec = tmp_path / "rec"
    ex.write(ex.build(str(repo), since=None, rules=[]), str(rec))
    try:
        se.deliver({"name": "drop", "type": "dir", "path": str(prefix)}, str(rec))
        raise AssertionError
    except ValueError as e:
        assert "not a sessions bundle" in str(e)


def test_cli_dry_run_writes_nothing_out_writes_to_delivers(repo, home, payload, monkeypatch, capsys, tmp_path):
    tp = _session(repo, home, payload)
    stop(payload("Stop", transcript=str(tp)), str(home))
    monkeypatch.chdir(repo)
    assert cli.main(["sessions", "export", "--dry-run", "--since", "1y"]) == 0
    out = capsys.readouterr().out
    assert "COMPLETE AND UNREDACTED" in out and "sess-1" in out
    assert not (repo / ".gitvow" / "out").exists()
    # no --out and no --to: say what would leave, write nothing
    assert cli.main(["sessions", "export"]) == 0
    err = capsys.readouterr().err
    assert "nothing written" in err and not (repo / ".gitvow" / "out").exists()
    dest = tmp_path / "b"
    assert cli.main(["sessions", "export", "--out", str(dest), "--json"]) == 0
    o = capsys.readouterr()
    assert (dest / "manifest.json").exists() and json.loads(o.out)["attestation"]["unredacted"] is True
    # deliver to a configured dir sink; a git sink refuses and the command says so
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (home / ".gitvow" / "sinks.json").write_text(
        json.dumps(
            {
                "sinks": [
                    {"name": "drop", "type": "dir", "path": str(prefix)},
                    {"name": "team", "type": "git", "path": str(tmp_path / "store")},
                ]
            }
        )
    )
    assert cli.main(["sessions", "export", "--to", "drop"]) == 0
    o = capsys.readouterr()
    assert "drop: delivered" in o.out and list((prefix / "github.com_acme_payments-api" / "sessions").iterdir())
    assert not (repo / ".gitvow" / "out").exists() or not list((repo / ".gitvow" / "out").iterdir())
    assert cli.main(["sessions", "export", "--to", "team"]) == 1
    assert "refused" in capsys.readouterr().out
    assert cli.main(["sessions", "export", "--to", "nope"]) == 1
    assert "no sink named" in capsys.readouterr().err
    # a session that is not there
    assert cli.main(["sessions", "export", "--session", "zzz", "--dry-run"]) == 1


def test_outside_a_repository_is_an_error(tmp_path, monkeypatch, home):
    monkeypatch.chdir(tmp_path)
    try:
        se.build(str(tmp_path), str(home))
        raise AssertionError
    except ValueError as e:
        assert "not inside a git repository" in str(e)
    assert subprocess.run(["git", "rev-parse"], cwd=tmp_path, capture_output=True).returncode != 0
