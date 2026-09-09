import json

from gitvow import cli
from gitvow.install import install_repo, install_user, uninstall_repo, uninstall_user
from tests.conftest import git


def test_user_install_is_idempotent_and_reversible(home):
    install_user(str(home))
    install_user(str(home))
    s = json.loads((home / ".claude" / "settings.json").read_text())
    assert set(s["hooks"]) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
    assert all(len(v) == 1 for v in s["hooks"].values())  # no duplicate entries after a second install
    assert git(home, "config", "--global", "--get", "core.hooksPath").endswith(".gitvow/git-hooks")
    assert git(home, "config", "--global", "--get-all", "notes.rewriteRef") == "refs/notes/gitvow/*"  # added once
    assert git(home, "config", "--global", "--get-all", "notes.displayRef") == "refs/notes/gitvow/*"
    uninstall_user(str(home), purge_policy=True)
    assert git(home, "config", "--global", "--get-all", "notes.rewriteRef") == ""
    assert not (home / ".claude" / "settings.json").exists()
    assert git(home, "config", "--global", "--get", "core.hooksPath") == ""
    assert not (home / ".gitvow" / "policy.json").exists()


def test_user_install_preserves_foreign_hooks(home):
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}, "theme": "dark"}
        )
    )
    install_user(str(home))
    uninstall_user(str(home))
    s = json.loads((home / ".claude" / "settings.json").read_text())
    assert s["theme"] == "dark" and s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo mine"


def test_repo_install_and_uninstall(repo, home):
    install_repo(str(repo))
    assert (repo / ".gitvow" / "policy.json").exists() and git(
        repo, "config", "--get", "core.hooksPath"
    ) == ".gitvow/git-hooks"
    assert git(repo, "config", "--get", "notes.displayRef") == "refs/notes/gitvow/*"
    (repo / ".git" / "gitvow-hooks.log").write_text("x\n")
    git(repo, "notes", "--ref=gitvow/a", "add", "-m", "n", "HEAD")
    git(repo, "notes", "--ref=gitvow/b", "add", "-m", "n", "HEAD")
    git(repo, "notes", "--ref=sessions", "add", "-m", "legacy", "HEAD")
    uninstall_repo(str(repo), purge_notes=True)
    assert git(repo, "for-each-ref", "refs/notes/") == ""
    assert git(repo, "config", "--get", "notes.displayRef") == ""
    assert not (repo / ".gitvow").exists() and git(repo, "config", "--get", "core.hooksPath") == ""
    assert not (repo / ".git" / "gitvow-hooks.log").exists()


def test_cli_check_and_version(capsys, monkeypatch, repo):
    monkeypatch.chdir(repo)
    assert cli.main(["check", "--", "git", "push", "--force"]) == 2
    assert "DENY" in capsys.readouterr().out
    assert cli.main(["check", "--", "ls"]) == 0
    assert cli.main(["check", "--path", "x/authz.py"]) == 0  # at-commit: recorded, asked on the card
    assert "CONFIRM AT COMMIT" in capsys.readouterr().out
    assert cli.main(["check", "--path", ".gitvow/policy.json"]) == 2  # gitvow's own policy stays immediate
    assert cli.main(["check", "--mcp", "mcp__a__delete_b"]) == 2


def test_cli_hook_roundtrip_and_show(capsys, monkeypatch, repo, home):
    monkeypatch.chdir(repo)
    import io

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {"session_id": "s9", "cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
            )
        ),
    )
    assert cli.main(["hook", "PreToolUse"]) == 2
    assert cli.main(["show", "HEAD"]) == 0
    assert "(no session note)" in capsys.readouterr().out


def test_selftest_passes(capsys):
    assert cli.main(["selftest"]) == 0
    assert "0 failed" in capsys.readouterr().out


def test_notes_follow_amend_and_rebase(repo, home):
    install_repo(str(repo))
    git(repo, "notes", "--ref=gitvow/s1", "add", "-m", "gitvow-session\n{}", "HEAD")
    git(repo, "commit", "-q", "--amend", "-m", "amended")
    assert git(repo, "notes", "--ref=gitvow/s1", "show", "HEAD").startswith("gitvow-session")
    assert "gitvow-session" in git(repo, "log", "-1", "--show-notes")  # displayRef makes plain git show it
    base = git(repo, "branch", "--show-current")
    git(repo, "checkout", "-qb", "topic")
    (repo / "t.txt").write_text("t\n")
    git(repo, "add", "t.txt")
    git(repo, "commit", "-qm", "topic")
    git(repo, "notes", "--ref=gitvow/s2", "add", "-m", 'gitvow-session\n{"step": 1}', "HEAD")
    git(repo, "checkout", "-q", base)
    (repo / "m.txt").write_text("m\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-qm", "main moves")
    git(repo, "checkout", "-q", "topic")
    git(repo, "rebase", "-q", base)
    assert git(repo, "notes", "--ref=gitvow/s2", "show", "HEAD").startswith("gitvow-session")


def test_user_install_does_not_duplicate_notes_config(home):
    install_user(str(home))
    install_user(str(home))
    assert git(home, "config", "--global", "--get-all", "notes.rewriteRef").count("refs/notes/gitvow/*") == 1


def test_show_reads_session_ref_then_legacy(capsys, monkeypatch, repo, home):
    monkeypatch.chdir(repo)
    git(repo, "notes", "--ref=sessions", "add", "-m", 'gitvow-session\n{"legacy": true}', "HEAD")
    assert cli.main(["show", "HEAD"]) == 0
    assert '"legacy": true' in capsys.readouterr().out
    (repo / "a.txt").write_text("n\n")
    git(repo, "commit", "-qam", "new\n\nGitvow-Session: s9\nGitvow-Step: 1")
    git(repo, "notes", "--ref=gitvow/s9", "add", "-m", 'gitvow-session\n{"step": 1}', "HEAD")
    assert cli.main(["show", "HEAD"]) == 0
    out = capsys.readouterr().out
    assert '"step": 1' in out and "Gitvow-Session: s9" in out


def test_cli_redact_uses_rules_and_fails_on_invalid(capsys, monkeypatch, repo, home):
    monkeypatch.chdir(repo)
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "redact-rules.json").write_text(
        json.dumps([{"pattern": r"CUST-\d{6}", "replacement": "[customer]"}])
    )
    assert cli.main(["redact", "ticket CUST-123456 for a@b.io"]) == 0
    out = capsys.readouterr().out
    assert "[customer]" in out and "[email:" in out
    (repo / ".gitvow" / "redact-rules.json").write_text("not json")
    assert cli.main(["redact", "x"]) == 2
    assert "invalid JSON" in capsys.readouterr().err


def test_default_policy_confirms_edits_to_redaction_rules(monkeypatch, repo, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["check", "--path", ".gitvow/redact-rules.json"]) == 2
    assert cli.main(["check", "--path", ".gitvow/git-hooks/prepare-commit-msg"]) == 2


def test_user_install_records_absolute_hook_command(home):
    from gitvow.install import executable_command

    install_user(str(home))
    s = json.loads((home / ".claude" / "settings.json").read_text())
    cmd = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert cmd == f"{executable_command()} hook PreToolUse"
    assert cmd.startswith("/") or " -m gitvow " in cmd  # never a bare name that depends on PATH
    uninstall_user(str(home))
    assert not (home / ".claude" / "settings.json").exists()  # marker still recognised with the absolute path


def test_repo_install_keeps_bare_name(repo, home):
    install_repo(str(repo))
    s = json.loads((repo / ".claude" / "settings.json").read_text())
    assert s["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "gitvow hook PreToolUse"


def test_python_m_gitvow_entrypoint():
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "gitvow", "--version"], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.startswith("gitvow ")
