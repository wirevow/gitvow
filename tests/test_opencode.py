"""0.33: OpenCode. A plugin gitvow writes, calling `gitvow hook --agent opencode` and throwing to block."""

import io
import json
import os
import shutil
import subprocess
import sys

import pytest

from gitvow import cli
from gitvow.adapters import AGENTS, normalize, respond
from gitvow.install import (
    MARKER,
    detect_agents,
    expected_events,
    hook_commands,
    install_repo,
    install_user,
    installed_agents,
    render_opencode_plugin,
    uninstall_repo,
    uninstall_user,
)
from gitvow.selftest import run_agent


def test_normalisation_maps_tools_and_keeps_ids():
    assert "opencode" in AGENTS
    calls = normalize(
        "opencode",
        "PreToolUse",
        {
            "session_id": "s",
            "cwd": "/r",
            "tool_name": "bash",
            "tool_input": {"command": "git push --force"},
            "tool_use_id": "c1",
        },
    )
    assert calls == [
        (
            "PreToolUse",
            {
                "session_id": "s",
                "transcript_path": "",
                "cwd": "/r",
                "hook_event_name": "PreToolUse",
                "agent": "opencode",
                "tool_use_id": "c1",
                "tool_name": "Bash",
                "tool_input": {"command": "git push --force"},
            },
        )
    ]
    ((_, p),) = normalize(
        "opencode",
        "PostToolUse",
        {
            "session_id": "s",
            "cwd": "/r",
            "tool_name": "edit",
            "tool_input": {"filePath": "/r/a.py", "oldString": "a", "newString": "b"},
        },
    )
    assert (
        p["tool_name"] == "Edit" and p["tool_input"]["file_path"] == "/r/a.py" and p["tool_input"]["new_string"] == "b"
    )
    ((_, p),) = normalize(
        "opencode",
        "PreToolUse",
        {"session_id": "s", "cwd": "/r", "tool_name": "write", "tool_input": {"filePath": "x", "content": "y"}},
    )
    assert p["tool_name"] == "Write" and p["tool_input"]["content"] == "y"
    ((_, p),) = normalize(
        "opencode",
        "PreToolUse",
        {"session_id": "s", "cwd": "/r", "tool_name": "patch", "tool_input": {"patchText": "..."}},
    )
    assert p["tool_name"] == "patch"  # passed through under its own name; the gate sees no path in it
    ((_, p),) = normalize(
        "opencode", "UserPromptSubmit", {"session_id": "s", "cwd": "/r", "prompt": "Let ops export orders"}
    )
    assert p["prompt"] == "Let ops export orders"
    assert normalize("opencode", "SessionStart", {"session_id": "s", "cwd": "/r"})[0][0] == "SessionStart"
    assert normalize("opencode", "unknown.event", {}) == []
    # responses are exit code and stderr; the plugin turns exit 2 into a thrown error
    assert respond("opencode", 2, "BLOCKED by policy (force push).") == (2, "", "BLOCKED by policy (force push).")
    assert respond("opencode", 0, "") == (0, "", "")


def test_install_writes_the_plugin_with_an_absolute_argv_and_uninstall_removes_only_ours(home, repo):
    out = install_user(str(home), cmd_prefix="/opt/bin/gitvow", agent="opencode")
    path = home / ".config" / "opencode" / "plugins" / "gitvow.js"
    assert path.exists() and any("hooks merged into ~/.config/opencode/plugins/gitvow.js" in x for x in out)
    assert any("throwing" in x for x in out)
    text = path.read_text()
    assert 'const GITVOW_HOOK = "/opt/bin/gitvow hook --agent opencode";' in text
    assert 'const ARGV = ["/opt/bin/gitvow", "hook", "--agent", "opencode"];' in text
    assert "export const GitvowPlugin" in text and "tool.execute.before" in text and "chat.message" in text
    assert hook_commands(str(path)) == [
        f"/opt/bin/gitvow hook --agent opencode {ev}"
        for ev in ("PostToolUse", "PreToolUse", "SessionStart", "Stop", "UserPromptSubmit")
    ]
    from gitvow.status import build as status_build

    checks = status_build(str(repo), str(home))
    oc = [c for c in checks if "opencode" in c[1]]
    assert oc and not any(c[0] == "fail" and "older gitvow" in c[1] for c in oc), oc
    assert ("opencode", "user", str(path)) in installed_agents(str(home))
    assert expected_events("opencode") == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
    install_user(str(home), cmd_prefix="/opt/bin/gitvow", agent="opencode")  # idempotent
    assert path.read_text() == text
    # a command with spaces is kept whole in argv
    assert '"python", "-m", "gitvow", "hook"' in render_opencode_plugin("python -m gitvow")
    uninstall_user(str(home), agent="opencode")
    assert not path.exists()
    # somebody else's plugin of the same name is not ours to delete
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("export const Mine = async () => ({})\n")
    uninstall_user(str(home), agent="opencode")
    assert path.exists() and MARKER not in path.read_text()
    # repository scope
    install_repo(str(repo), cmd_prefix="/opt/bin/gitvow", agent="opencode")
    assert (repo / ".opencode" / "plugins" / "gitvow.js").exists()
    uninstall_repo(str(repo), agent="opencode")
    assert not (repo / ".opencode" / "plugins" / "gitvow.js").exists()
    (home / ".config" / "opencode").mkdir(parents=True, exist_ok=True)
    assert "opencode" in detect_agents(str(home))


def test_hook_cli_and_selftest(repo, home, monkeypatch, capsys):
    p = {
        "session_id": "s1",
        "cwd": str(repo),
        "tool_name": "bash",
        "tool_input": {"command": "git push --force"},
        "tool_use_id": "c1",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(p)))
    assert cli.main(["hook", "--agent", "opencode", "PreToolUse"]) == 2
    assert "BLOCKED" in capsys.readouterr().err
    assert run_agent("opencode") == 0
    out = capsys.readouterr().out
    assert "opencode: force push denied in the agent's own form" in out and "FAIL" not in out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is needed to load the plugin")
def test_the_plugin_itself_blocks_records_and_lets_through(repo, home, tmp_path):
    """Load the JavaScript OpenCode will load, in node, against the real gitvow, in a scratch repository."""
    gitvow = f"{sys.executable} -m gitvow"
    install_user(str(home), cmd_prefix=gitvow, agent="opencode")
    plugin = home / ".config" / "opencode" / "plugins" / "gitvow.js"
    driver = tmp_path / "drive.mjs"
    driver.write_text(
        f"""
import {{ GitvowPlugin }} from {json.dumps(str(plugin))};
const hooks = await GitvowPlugin({{ directory: {json.dumps(str(repo))}, worktree: {json.dumps(str(repo))} }});
const out = {{}};
await hooks.event({{ event: {{ type: "session.created", properties: {{ info: {{ id: "oc-1" }} }} }} }});
await hooks["chat.message"]({{ sessionID: "oc-1" }}, {{ parts: [{{ type: "text", text: "Let ops export orders as CSV from the admin page." }}] }});
try {{
  await hooks["tool.execute.before"]({{ tool: "bash", sessionID: "oc-1", callID: "c1" }}, {{ args: {{ command: "git push --force" }} }});
  out.force = "allowed";
}} catch (e) {{ out.force = e.message; }}
await hooks["tool.execute.before"]({{ tool: "edit", sessionID: "oc-1", callID: "c2" }}, {{ args: {{ filePath: {json.dumps(str(repo / "a.txt"))}, oldString: "a", newString: "b" }} }});
out.edit = "allowed";
await hooks["tool.execute.after"]({{ tool: "edit", sessionID: "oc-1", callID: "c2", args: {{ filePath: {json.dumps(str(repo / "a.txt"))} }} }}, {{ title: "", output: "", metadata: {{}} }});
await hooks.event({{ event: {{ type: "session.idle", properties: {{ sessionID: "oc-1" }} }} }});
console.log(JSON.stringify(out));
"""
    )
    env = {**os.environ, "HOME": str(home), "GIT_CONFIG_GLOBAL": str(home / ".gitconfig")}
    r = subprocess.run(["node", str(driver)], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["force"].startswith("BLOCKED") and got["edit"] == "allowed"
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["session_id"] == "oc-1"
    assert (
        st["intent"]["text"] == "Let ops export orders as CSV from the admin page."
        and st["intent"]["source"] == "prompt"
    )
    assert st.get("agent_blobs"), "the edit's blob was recorded for attribution"
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "blocked"' in log and '"kind": "session_stop"' in log
    assert (home / ".gitvow" / "ledger" / "oc-1.json").exists()
    # a missing gitvow fails closed: the plugin throws before the tool runs
    install_user(str(home), cmd_prefix="/nonexistent/gitvow", agent="opencode")
    driver.write_text(
        f"""
import {{ GitvowPlugin }} from {json.dumps(str(plugin))};
const hooks = await GitvowPlugin({{ directory: {json.dumps(str(repo))}, worktree: {json.dumps(str(repo))} }});
try {{ await hooks["tool.execute.before"]({{ tool: "bash", sessionID: "x", callID: "c" }}, {{ args: {{ command: "ls" }} }}); console.log("allowed"); }}
catch (e) {{ console.log("thrown: " + e.message); }}
"""
    )
    r = subprocess.run(["node", str(driver)], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0 and r.stdout.strip().startswith("thrown: gitvow hook did not run")
