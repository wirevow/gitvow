import io
import json
import os
import shutil
import stat
from pathlib import Path

from gitvow import cli
from gitvow.adapters import AdapterError, external_info, external_normalize, external_path, external_respond
from gitvow.install import install_repo, install_user, uninstall_user

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "agents" / "gitvow-agent-example"


def _on_path(tmp_path, monkeypatch, name="gitvow-agent-example", src=EXAMPLE):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    dst = d / name
    shutil.copy(src, dst)
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ.get('PATH', '')}")
    return dst


def test_discovery_and_protocol_calls(tmp_path, monkeypatch, home):
    assert external_path("example") is None
    assert external_path("claude") is None  # built-ins never resolve externally
    assert external_path("../evil") is None
    exe = _on_path(tmp_path, monkeypatch)
    assert external_path("example") == str(exe)
    info = external_info(str(exe))
    assert info["protocol"] == 1 and "beforeTool" in info["events"]
    calls = external_normalize(
        str(exe), "beforeTool", {"sid": "x1", "dir": "/w", "tool": "sh", "args": {"cmd": "rm -rf /"}}
    )
    assert calls == [
        (
            "PreToolUse",
            {
                "session_id": "x1",
                "transcript_path": "",
                "cwd": "/w",
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
            },
        )
    ]
    assert external_normalize(str(exe), "unknownEvent", {}) == []
    code, out, _err = external_respond(str(exe), 2, "BLOCKED by policy (x).")
    assert code == 0 and json.loads(out)["verdict"] == "deny"
    # ~/.gitvow/agents fallback
    (home / ".gitvow" / "agents").mkdir(parents=True)
    shutil.copy(EXAMPLE, home / ".gitvow" / "agents" / "gitvow-agent-other")
    (home / ".gitvow" / "agents" / "gitvow-agent-other").chmod(0o700)
    assert external_path("other", str(home)) == str(home / ".gitvow" / "agents" / "gitvow-agent-other")


def test_hook_cli_with_external_agent_and_fail_closed(repo, home, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    _on_path(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"sid": "x1", "dir": str(repo), "tool": "sh", "args": {"cmd": "git push --force"}})),
    )
    assert cli.main(["hook", "--agent", "example", "beforeTool"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "deny"
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"sid": "x1", "dir": str(repo), "tool": "sh", "args": {"cmd": "kubectl apply -f x"}})),
    )
    cli.main(["hook", "--agent", "example", "beforeTool"])
    assert json.loads(capsys.readouterr().out)["verdict"] == "ask"
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps({"sid": "x1", "dir": str(repo), "tool": "sh", "args": {"cmd": "ls"}}))
    )
    cli.main(["hook", "--agent", "example", "beforeTool"])
    assert json.loads(capsys.readouterr().out)["verdict"] == "allow"
    # afterTool edit takes a snapshot like any built-in adapter
    (repo / "a.txt").write_text("ext\n")
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "sid": "x1",
                    "dir": str(repo),
                    "tool": "edit",
                    "args": {"path": str(repo / "a.txt"), "before": "a", "after": "ext"},
                }
            )
        ),
    )
    assert cli.main(["hook", "--agent", "example", "afterTool"]) == 0
    from tests.conftest import git

    assert git(repo, "for-each-ref", "refs/gitvow/snapshots/").strip() != ""
    # a broken adapter blocks rather than passing the call through
    broken = tmp_path / "bin" / "gitvow-agent-broken"
    broken.write_text("#!/bin/sh\necho not-json\n")
    broken.chmod(0o700)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"anything": 1})))
    assert cli.main(["hook", "--agent", "broken", "beforeTool"]) == 2
    assert "adapter failed" in capsys.readouterr().err
    # unknown agent with no executable
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    assert cli.main(["install", "--user", "--agent", "nosuch"]) == 1
    assert "unknown agent" in capsys.readouterr().err


def test_install_from_info(repo, home, tmp_path, monkeypatch):
    _on_path(tmp_path, monkeypatch)
    install_user(str(home), agent="example")
    install_user(str(home), agent="example")
    s = json.loads((home / ".example-agent" / "hooks.json").read_text())
    assert s["version"] == 1 and len(s["hooks"]["beforeTool"]) == 1
    assert "hook --agent example beforeTool" in s["hooks"]["beforeTool"][0][
        "command"
    ] and "__GITVOW__" not in json.dumps(s)
    uninstall_user(str(home), agent="example")
    assert not (home / ".example-agent" / "hooks.json").exists()
    install_repo(str(repo), agent="example")
    assert (repo / ".example-agent" / "hooks.json").exists()


def test_bad_protocol_versions_and_shapes(tmp_path, monkeypatch):
    d = tmp_path / "bin"
    d.mkdir()
    bad = d / "gitvow-agent-bad"
    bad.write_text(
        '#!/bin/sh\ncase "$1" in info) echo \'{"protocol": 9}\';; normalize) echo \'{"calls": [{"event": "Nope", "payload": {}}]}\';; *) echo \'[]\';; esac\n'
    )
    bad.chmod(0o700)
    for fn, args in ((external_info, ()), (external_normalize, ("x", {})), (external_respond, (0, ""))):
        try:
            fn(str(bad), *args)
            raise AssertionError("expected AdapterError")
        except AdapterError:
            pass
