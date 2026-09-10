import json
import os
import subprocess
import time
from pathlib import Path

from gitvow import cli
from gitvow.install import install_repo, install_user
from gitvow.status import build, render


def _first(checks, needle):
    return next((c for c in checks if needle in c[1]), None)


def test_a_clean_install_reports_nothing_failing(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_user(str(home))
    install_repo(str(repo))
    checks = build(str(repo), str(home))
    assert not [c for c in checks if c[0] == "fail"], checks
    assert _first(checks, "policy loads")[0] == "ok"
    assert _first(checks, "redaction rules load")[0] == "ok"
    assert "prepare-commit-msg, pre-push, pre-commit, post-commit" in _first(checks, "git hooks in")[1]
    # no session has run in this fixture repo yet, and status says so rather than implying it works
    assert _first(checks, "no hook log")[0] == "note"
    text = render(checks)
    assert text.startswith("gitvow ") and "Ready: run a session and commit" in text


def test_an_unreachable_hook_command_fails_loudly(repo, home, monkeypatch):
    """The trap a real Cursor session hit: hooks installed, executable not findable."""
    monkeypatch.chdir(repo)
    install_repo(str(repo), cmd_prefix="/nonexistent/bin/gitvow")
    c = _first(build(str(repo), str(home)), "hook command cannot be run")
    assert c and c[0] == "fail" and "records the absolute path" in c[2]


def test_a_bare_command_is_flagged_as_path_dependent(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_repo(str(repo))  # per-repo installs keep the bare name, because the file is shared
    c = _first(build(str(repo), str(home)), "from PATH")
    assert c and c[0] == "note" and "Finder" in c[2]


def test_nothing_installed_fails_on_both_counts(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    checks = build(str(repo), str(home))
    assert _first(checks, "no agent has gitvow hooks installed")[0] == "fail"
    assert _first(checks, "core.hooksPath is not set")[0] == "fail"
    assert "no commit gets a trailer" in _first(checks, "core.hooksPath is not set")[1]


def test_a_broken_policy_is_the_loudest_failure(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_repo(str(repo))
    (repo / ".gitvow" / "policy.json").write_text("{not json")
    c = _first(build(str(repo), str(home)), "policy will not load")
    assert c and c[0] == "fail" and "every tool call is refused" in c[1]


def test_codex_gets_its_two_manual_steps(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_repo(str(repo), agent="codex")
    checks = build(str(repo), str(home))
    trust = _first(checks, "skipped until trusted")
    assert trust and trust[0] == "note" and "/hooks" in trust[2]
    sandbox = _first(checks, "refuses writes inside .git")
    assert sandbox and sandbox[0] == "note" and str(repo) in sandbox[2]
    os.makedirs(home / ".codex", exist_ok=True)
    (home / ".codex" / "config.toml").write_text("[features]\nhooks = false\n")
    off = _first(build(str(repo), str(home)), "switched off")
    assert off and off[0] == "fail"


def test_cli_status_exit_code_and_json(repo, home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["status"]) == 1  # nothing installed
    assert "FAIL" in capsys.readouterr().out
    install_user(str(home))
    install_repo(str(repo))
    assert cli.main(["status"]) == 0
    capsys.readouterr()
    assert cli.main(["status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {r["state"] for r in rows} <= {"ok", "fail", "note"}
    assert any(r["what"].startswith("policy loads") for r in rows)


def test_install_prints_the_codex_steps(repo, home):
    out = install_user(str(home), agent="codex")
    assert any("/hooks" in x and "trust" in x for x in out)
    assert not [x for x in install_user(str(home)) if "/hooks" in x]  # claude has no extra step
    assert Path(home, ".claude", "settings.json").exists()


def test_install_without_an_agent_configures_everything_it_finds(repo, home, monkeypatch, capsys):
    """Forgetting --agent must not silently leave an agent ungated."""
    from gitvow.install import detect_agents

    monkeypatch.chdir(repo)
    for d in (".codex", ".cursor"):
        os.makedirs(home / d, exist_ok=True)
    assert set(detect_agents(str(home))) >= {"codex", "cursor"}
    assert cli.main(["install", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "agents found here:" in out and "codex" in out and "cursor" in out
    assert (repo / ".codex" / "hooks.json").exists() and (repo / ".cursor" / "hooks.json").exists()
    assert "next: run `gitvow status`" in out
    # and uninstall with no --agent clears every one of them, leaving any hooks of your own in place
    from gitvow.install import hook_commands

    assert cli.main(["uninstall", str(repo)]) == 0
    for rel in (".codex/hooks.json", ".cursor/hooks.json", ".claude/settings.json"):
        assert hook_commands(str(repo / rel)) == [], rel


def test_an_old_install_is_reported_as_stale(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_repo(str(repo))
    hooks = repo / ".gitvow" / "git-hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\n# an older gitvow wrote this\nexit 0\n")
    c = _first(build(str(repo), str(home)), "written by an older gitvow")
    assert c and c[0] == "fail" and "pre-commit" in c[1] and "re-run" in c[2]
    # an agent settings file from before an event existed
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    settings["hooks"].pop("Stop", None)
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    c = _first(build(str(repo), str(home)), "installed by an older gitvow")
    assert c and c[0] == "fail" and "Stop" in c[1]


def _signed_commit(repo, name, body, offset=None):
    """`offset` in seconds places the commit unambiguously before or after an install.

    Both git's --since and a commit timestamp have one-second granularity, so a test that commits in the
    same second as the install cannot say which came first.
    """
    (repo / name).write_text("x\n")
    env = dict(os.environ)
    if offset is not None:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + offset))
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", body], cwd=repo, check=True, capture_output=True, env=env)


def test_an_agent_commit_since_the_install_with_no_session_fails(repo, home, monkeypatch):
    """The miss this check exists for: hooks are read at start-up, so a session already open is ungated."""
    monkeypatch.chdir(repo)
    install_repo(str(repo))
    _signed_commit(repo, "one.txt", "made by an ungated session\n\nCo-authored-by: Claude <x@y>", offset=3600)
    c = _first(build(str(repo), str(home)), "since the install")
    assert c and c[0] == "fail"
    assert "1 agent commit" in c[1] and "carries no session" in c[1]
    assert "already open when you installed" in c[2] and "Cursor reloads by itself" in c[2]
    assert "the record is not being written" in render(build(str(repo), str(home)))


def test_a_recorded_commit_since_the_install_is_silent(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_repo(str(repo))
    _signed_commit(
        repo,
        "one.txt",
        "recorded\n\nCo-authored-by: Claude <x@y>\nGitvow-Session: s1\nGitvow-Step: 1",
        offset=3600,
    )
    checks = build(str(repo), str(home))
    assert _first(checks, "since the install") is None
    assert not [c for c in checks if c[0] == "fail"], checks


def test_commits_before_the_install_are_not_blamed_on_it(repo, home, monkeypatch):
    """Existing history is uncovered by construction; only what happens after the install is a signal."""
    monkeypatch.chdir(repo)
    _signed_commit(repo, "old.txt", "before gitvow existed here\n\nCo-authored-by: Claude <x@y>", offset=-7200)
    install_repo(str(repo))
    assert _first(build(str(repo), str(home)), "since the install") is None


def test_status_no_longer_repeats_the_restart_advice(repo, home, monkeypatch):
    """It is install-time guidance, and status can test for the real thing instead."""
    monkeypatch.chdir(repo)
    out = install_repo(str(repo), agent="codex")
    assert any("restart it if a session is already open" in x for x in out)
    assert not [c for c in build(str(repo), str(home)) if "restart it if a session" in c[1]]
    assert not [x for x in install_repo(str(repo), agent="cursor") if "restart it" in x]
