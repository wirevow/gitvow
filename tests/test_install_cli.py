import json

from provkit import cli
from provkit.install import install_repo, install_user, uninstall_repo, uninstall_user
from tests.conftest import git


def test_user_install_is_idempotent_and_reversible(home):
    install_user(str(home))
    install_user(str(home))
    s = json.loads((home / ".claude" / "settings.json").read_text())
    assert set(s["hooks"]) == {"SessionStart", "PreToolUse", "PostToolUse", "Stop"}
    assert all(len(v) == 1 for v in s["hooks"].values())  # no duplicate entries after a second install
    assert git(home, "config", "--global", "--get", "core.hooksPath").endswith(".provkit/git-hooks")
    uninstall_user(str(home), purge_policy=True)
    assert not (home / ".claude" / "settings.json").exists()
    assert git(home, "config", "--global", "--get", "core.hooksPath") == ""
    assert not (home / ".provkit" / "policy.json").exists()


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
    assert (repo / ".provkit" / "policy.json").exists() and git(
        repo, "config", "--get", "core.hooksPath"
    ) == ".provkit/git-hooks"
    (repo / ".git" / "provkit-hooks.log").write_text("x\n")
    uninstall_repo(str(repo), purge_notes=True)
    assert not (repo / ".provkit").exists() and git(repo, "config", "--get", "core.hooksPath") == ""
    assert not (repo / ".git" / "provkit-hooks.log").exists()


def test_cli_check_and_version(capsys, monkeypatch, repo):
    monkeypatch.chdir(repo)
    assert cli.main(["check", "--", "git", "push", "--force"]) == 2
    assert "DENY" in capsys.readouterr().out
    assert cli.main(["check", "--", "ls"]) == 0
    assert cli.main(["check", "--path", "x/authz.py"]) == 2
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
