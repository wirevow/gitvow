"""The collector (0.22): bundles move to sinks configured in files that are never committed; outbox when unreachable."""

import json
import os
import subprocess

from gitvow import cli
from gitvow import sync as sy
from tests.conftest import git


def _decided(repo, name, trailer):
    (repo / f"{name}.txt").write_text(name + "\n")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-qm", f"{name}\n\nGitvow-Session: s1\nGitvow-Step: 1\n{trailer}\n")


def _sinks(repo, home, rows, where="repo"):
    if where == "repo":
        (repo / ".gitvow").mkdir(exist_ok=True)
        (repo / ".gitvow" / "export.local.json").write_text(json.dumps({"sinks": rows}))
    else:
        (home / ".gitvow").mkdir(exist_ok=True)
        (home / ".gitvow" / "sinks.json").write_text(json.dumps({"sinks": rows}))


def test_sinks_come_from_local_files_only_and_a_committed_one_is_ignored(repo, home, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    assert sy.sinks(str(repo), str(home)) == []
    out = sy.sync(str(repo), str(home), rules=[])
    assert out["results"] == [] and "no sinks configured" in out["warnings"][0]
    _sinks(repo, home, [{"name": "acme", "type": "dir", "path": str(tmp_path / "prefix")}])
    _sinks(repo, home, [{"name": "mine", "type": "git", "path": "~/store"}], where="home")
    s = sy.sinks(str(repo), str(home))
    assert [x["name"] for x in s] == ["acme", "mine"] and s[0]["scope"] == "repo" and s[1]["scope"] == "home"
    assert s[1]["path"] == os.path.expanduser("~/store")
    # a sink named in the committed consent file is ignored with a warning
    (repo / ".gitvow" / "export.json").write_text(
        json.dumps({"consent": {"decisions": True}, "sinks": [{"name": "upstream", "type": "dir", "path": "/tmp/x"}]})
    )
    (tmp_path / "prefix").mkdir()
    out = sy.sync(str(repo), str(home), only="acme", rules=[])
    assert any("never read from a committed file" in w for w in out["warnings"])
    assert [r["sink"] for r in out["results"]] == ["acme"]
    try:
        sy.sync(str(repo), str(home), only="nope", rules=[])
        raise AssertionError
    except ValueError as e:
        assert "no sink named" in str(e)


def test_dir_sink_delivers_idempotently_and_git_sink_commits(repo, home, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    _decided(repo, "c1", "Gitvow-Accepted: edit core/authz_rules.go by priya")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=store, check=True)
    subprocess.run(
        ["git", "-c", "user.email=s@x", "-c", "user.name=s", "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=store,
        check=True,
    )
    _sinks(
        repo,
        home,
        [{"name": "acme", "type": "dir", "path": str(prefix)}, {"name": "st", "type": "git", "path": str(store)}],
    )
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    digest = out["bundle"]["digest"].split(":")[-1]
    statuses = {r["sink"]: r["status"] for r in out["results"]}
    assert statuses == {"acme": "delivered", "st": "delivered"}
    assert (prefix / "github.com_acme_payments-api" / digest / "manifest.json").exists()
    assert (store / "bundles" / "github.com_acme_payments-api" / digest / "decisions.jsonl").exists()
    with open(store / "frontiers" / "github.com_acme_payments-api.jsonl") as fh:
        front = [json.loads(line) for line in fh]
    assert front[0]["digest"] == digest and front[0]["via"] == "gitvow sync"
    log = subprocess.run(["git", "log", "--format=%s"], cwd=store, capture_output=True, text=True).stdout
    assert log.splitlines()[0].startswith("store: receive github.com/acme/payments-api")
    # the same record again: nothing resent, no new commit
    out2 = sy.sync(str(repo), str(home), since=None, rules=[])
    assert {r["status"] for r in out2["results"]} == {"already held"}
    assert subprocess.run(["git", "log", "--format=%s"], cwd=store, capture_output=True, text=True).stdout == log
    # the staging directory is cleaned up
    assert not os.path.isdir(os.path.join(str(home), ".gitvow", "outbox", ".staging", digest))


def test_unreachable_sink_queues_in_the_outbox_and_drains_later(repo, home, tmp_path):
    git(repo, "remote", "add", "origin", "https://github.com/acme/ledger-svc.git")
    _decided(repo, "c1", "Gitvow-Declined: edit .github/workflows/ci.yml by dmitri")
    prefix = tmp_path / "later"  # does not exist yet
    _sinks(repo, home, [{"name": "later", "type": "dir", "path": str(prefix)}])
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    r = out["results"][0]
    assert r["status"] == "unreachable" and r["queued_at"].startswith(
        os.path.join(str(home), ".gitvow", "outbox", "later")
    )
    assert os.path.exists(os.path.join(r["queued_at"], "manifest.json"))
    # a second record while still unreachable: the second bundle waits too
    _decided(repo, "c2", "Gitvow-Accepted: edit core/authz_rules.go by priya")
    out2 = sy.sync(str(repo), str(home), since=None, rules=[])
    assert out2["results"][0]["status"] == "unreachable"
    box = os.path.join(str(home), ".gitvow", "outbox", "later", "github.com_acme_ledger-svc")
    assert len(os.listdir(box)) == 2
    # the sink appears: the next sync drains the outbox oldest first, then delivers the current record
    prefix.mkdir()
    out3 = sy.sync(str(repo), str(home), since=None, rules=[])
    kinds = [(r["status"], r.get("from_outbox", False)) for r in out3["results"]]
    assert kinds[:2] == [("delivered", True), ("delivered", True)] and kinds[-1][0] == "already held"
    assert not os.listdir(box)
    assert len(os.listdir(prefix / "github.com_acme_ledger-svc")) == 2


def test_refuses_to_move_anything_but_the_record(tmp_path):
    bad = tmp_path / "raw"
    bad.mkdir()
    (bad / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "sessions-raw",
                "source": {"remote": "x"},
                "digest": "sha256:ab",
                "frontier": {"head": "a", "as_of": "t"},
            }
        )
    )
    dest = tmp_path / "prefix"
    dest.mkdir()
    try:
        sy.deliver({"name": "d", "type": "dir", "path": str(dest)}, str(bad))
        raise AssertionError
    except ValueError as e:
        assert "carries 'record' only" in str(e)


def test_cli_sinks_and_sync(repo, home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    assert cli.main(["sinks"]) == 0 and "No sinks configured" in capsys.readouterr().out
    prefix = tmp_path / "p"
    prefix.mkdir()
    _sinks(repo, home, [{"name": "acme", "type": "dir", "path": str(prefix)}])
    assert cli.main(["sinks"]) == 0 and "acme: dir" in capsys.readouterr().out
    assert cli.main(["sync", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would deliver" in out and not os.listdir(prefix)
    assert cli.main(["sync", "--since", "1y"]) == 0
    assert "acme: delivered" in capsys.readouterr().out and os.listdir(prefix)
    # install keeps the local sink file out of the index
    assert cli.main(["install", str(repo)]) == 0
    capsys.readouterr()
    assert ".gitvow/export.local.json" in (repo / ".git" / "info" / "exclude").read_text()
    assert ".gitvow/export.local.json" not in git(repo, "status", "--porcelain", "--untracked-files=all")
