import json
import os
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
    assert "prepare-commit-msg, pre-commit, post-commit, pre-push" in _first(checks, "git hooks in")[1]
    # no session has run in this fixture repo yet, and status says so rather than implying it works
    assert _first(checks, "no hook log")[0] == "note"
    text = render(checks)
    assert text.startswith("gitvow ") and "nothing failing" in text


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
    assert any("gitvow status" in x for x in out)
    assert not [x for x in install_user(str(home)) if "/hooks" in x]  # claude has no extra step
    assert Path(home, ".claude", "settings.json").exists()
