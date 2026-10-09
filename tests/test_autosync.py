"""Automatic sync (0.37): policy `sync.automatic` starts the collector in the background at session end and after
the agent's push, when a sink is configured; off by default; never in the hook's own time; never recursive."""

import json
import subprocess
import time

from gitvow import sync as sy
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git


def _policy(repo, **sync):
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"sync": sync}))


def _sink(home, path):
    (home / ".gitvow").mkdir(exist_ok=True)
    (home / ".gitvow" / "sinks.json").write_text(
        json.dumps({"sinks": [{"name": "drop", "type": "dir", "path": str(path)}]})
    )


def test_off_by_default_and_needs_a_sink(repo, home, monkeypatch):
    calls = []
    monkeypatch.setattr(sy, "_spawn", lambda argv, cwd, env, log, when: calls.append((argv, cwd, env, when)) or 1)
    assert sy.auto(str(repo), str(home), "session_end", {}) is None
    assert sy.auto(str(repo), str(home), "session_end", {"sync": {"automatic": True}}) is None  # no sink
    _sink(home, repo.parent / "prefix")
    assert sy.auto(str(repo), str(home), "session_end", {}) is None  # default off
    assert sy.auto(str(repo), str(home), "session_end", {"sync": {"automatic": True, "on_session_end": False}}) is None
    assert sy.auto(str(repo), str(home), "push", {"sync": {"automatic": True, "on_push": False}}) is None
    assert calls == []
    started = sy.auto(str(repo), str(home), "push", {"sync": {"automatic": True, "since": "30d"}})
    assert started and started["when"] == "push" and started["since"] == "30d" and len(calls) == 1
    argv = calls[0][0]
    assert argv[1:] == ["-m", "gitvow", "sync", "--since", "30d"] and calls[0][3] == "push"
    # the child cannot start another
    monkeypatch.setenv(sy.AUTOSYNC_ENV, "push")
    assert sy.auto(str(repo), str(home), "push", {"sync": {"automatic": True}}) is None and len(calls) == 1
    # a broken sink file is not a reason to crash a hook
    monkeypatch.delenv(sy.AUTOSYNC_ENV)
    (home / ".gitvow" / "sinks.json").write_text(json.dumps({"sinks": [{"name": "x", "type": "nope"}]}))
    assert sy.auto(str(repo), str(home), "push", {"sync": {"automatic": True}}) is None


def _wait_for(pred, seconds=30):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if pred():
            return True
        time.sleep(0.2)
    return False


def test_session_end_really_syncs_in_the_background(repo, home, payload, transcript, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    _sink(home, prefix)
    _policy(repo, automatic=True, since="1y")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(
        repo,
        "commit",
        "-qm",
        "b\n\nGitvow-Session: s1\nGitvow-Step: 1\nGitvow-Accepted: edit core/authz_rules.go by priya\n",
    )
    session_start(payload("SessionStart"), str(home))
    t0 = time.time()
    code, _ = stop(payload("Stop", transcript=transcript), str(home))
    assert code == 0 and time.time() - t0 < 5  # the hook did not wait for the sync
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "auto_sync"' in log and '"when": "session_end"' in log
    assert _wait_for(lambda: (prefix / "github.com_acme_payments-api").is_dir()), (
        home / ".gitvow" / "sync.log"
    ).read_text()
    bundles = list((prefix / "github.com_acme_payments-api").iterdir())
    assert len(bundles) == 1 and (bundles[0] / "decisions.jsonl").exists()
    assert "session_end" in (home / ".gitvow" / "sync.log").read_text()
    # a second session end finds the bundle already held; nothing new appears
    stop(payload("Stop", transcript=transcript), str(home))
    assert _wait_for(lambda: "already held" in (home / ".gitvow" / "sync.log").read_text())
    assert len(list((prefix / "github.com_acme_payments-api").iterdir())) == 1


def test_push_by_the_agent_triggers_it_and_sessions_never_do(repo, home, payload, tmp_path, monkeypatch):
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    git(repo, "remote", "add", "origin", str(bare))
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    _sink(home, prefix)
    _policy(repo, automatic=True)
    calls = []
    monkeypatch.setattr(sy, "_spawn", lambda argv, cwd, env, log, when: calls.append((argv, cwd, env, when)) or 7)
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    session_start(payload("SessionStart"), str(home))
    (repo / "a.txt").write_text("agent\n")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push origin HEAD:main"}), str(home))
    git(repo, "push", "-q", "origin", "HEAD:main")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git push origin HEAD:main"}), str(home))
    assert len(calls) == 1 and calls[0][0][3] == "sync" and calls[0][1] == str(repo)
    assert calls[0][2][sy.AUTOSYNC_ENV] == "push" and calls[0][3] == "push"
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"when": "push"' in log
    # nothing in the automatic path knows about sessions export: the argv is the record sync, and only that
    assert "sessions" not in " ".join(calls[0][0])
