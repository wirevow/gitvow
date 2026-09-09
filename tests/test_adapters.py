import io
import json

from gitvow import cli
from gitvow.adapters import normalize, parse_apply_patch, respond
from gitvow.install import install_repo, install_user, uninstall_repo, uninstall_user

PATCH = """*** Begin Patch
*** Update File: src/api/Orders.java
@@
-    return old;
+    @Path("/v1/orders/export")
+    return csv;
*** Add File: docs/new.md
+# new
*** Delete File: old.txt
*** End Patch
"""


def test_parse_apply_patch():
    files = parse_apply_patch(PATCH)
    assert [f["file_path"] for f in files] == ["src/api/Orders.java", "docs/new.md", "old.txt"]
    assert (
        files[0]["op"] == "update"
        and "/v1/orders/export" in files[0]["new_string"]
        and "return old;" in files[0]["old_string"]
    )
    assert files[1]["op"] == "add" and files[1]["new_string"] == "# new"
    assert files[2]["op"] == "delete"


def test_codex_normalization_expands_patch_and_maps_events():
    calls = normalize(
        "codex",
        "PreToolUse",
        {"session_id": "c1", "cwd": "/r", "tool_name": "apply_patch", "tool_input": {"command": PATCH}},
    )
    assert [c[0] for c in calls] == ["PreToolUse"] * 3
    assert calls[0][1]["tool_name"] == "Edit" and calls[0][1]["tool_input"]["file_path"] == "src/api/Orders.java"
    bash = normalize(
        "codex", "PreToolUse", {"session_id": "c1", "cwd": "/r", "tool_name": "Bash", "tool_input": {"command": "ls"}}
    )
    assert bash[0][1]["tool_name"] == "Bash" and bash[0][1]["tool_input"] == {"command": "ls"}
    assert normalize("codex", "Stop", {"session_id": "c1", "cwd": "/r"})[0][0] == "Stop"
    assert normalize("codex", "UserPromptSubmit", {}) == []


def test_gemini_normalization():
    c = normalize(
        "gemini",
        "BeforeTool",
        {"session_id": "g1", "cwd": "/r", "tool_name": "run_shell_command", "tool_input": {"command": "git push -f"}},
    )
    assert c[0][0] == "PreToolUse" and c[0][1]["tool_name"] == "Bash"
    c = normalize(
        "gemini",
        "AfterTool",
        {
            "session_id": "g1",
            "cwd": "/r",
            "tool_name": "write_file",
            "tool_input": {"file_path": "a.py", "content": "x"},
        },
    )
    assert c[0][0] == "PostToolUse" and c[0][1]["tool_name"] == "Write"
    assert normalize("gemini", "SessionEnd", {"session_id": "g1", "cwd": "/r"})[0][0] == "Stop"


def test_cursor_normalization_and_responses():
    c = normalize(
        "cursor", "beforeShellExecution", {"conversation_id": "cu1", "workspace_roots": ["/w"], "command": "rm -rf /"}
    )
    assert c[0][1] == {
        "session_id": "cu1",
        "transcript_path": "",
        "cwd": "/w",
        "hook_event_name": "PreToolUse",
        "agent": "cursor",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    c = normalize(
        "cursor",
        "afterFileEdit",
        {
            "conversation_id": "cu1",
            "workspace_roots": ["/w"],
            "file_path": "/w/a.py",
            "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "", "new_string": '"/v1/x"'}],
        },
    )
    assert c[0][0] == "PostToolUse" and c[0][1]["tool_input"]["new_string"] == 'b\n"/v1/x"'
    c = normalize(
        "cursor",
        "beforeMCPExecution",
        {
            "conversation_id": "cu1",
            "workspace_roots": ["/w"],
            "tool_name": "delete_dashboard",
            "mcp_server_name": "grafana",
            "tool_input": {},
        },
    )
    assert c[0][1]["tool_name"] == "mcp__grafana__delete_dashboard"
    assert respond("cursor", 2, "BLOCKED by policy (force push).")[1] == json.dumps(
        {
            "permission": "deny",
            "user_message": "BLOCKED by policy (force push).",
            "agent_message": "BLOCKED by policy (force push).",
        }
    )
    assert json.loads(respond("cursor", 2, "CONFIRMATION REQUIRED (x). Ask the user")[1])["permission"] == "ask"
    assert json.loads(respond("cursor", 0, "")[1]) == {"permission": "allow"}
    assert respond("codex", 2, "BLOCKED")[0] == 2 and respond("gemini", 0, "")[0] == 0


def _run_hook(monkeypatch, capsys, agent, event, payload):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    code = cli.main(["hook", "--agent", agent, event])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_cli_hook_per_agent_end_to_end(repo, home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    # codex: a destructive command is blocked with exit 2; a patch touching a gate file requires confirmation naming the file
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "codex",
        "PreToolUse",
        {"session_id": "c1", "cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": "git push --force"}},
    )
    assert code == 2 and "BLOCKED" in err and out == ""
    patch = "*** Begin Patch\n*** Update File: core/authz_rules.go\n+x\n*** Update File: README.md\n+y\n*** End Patch\n"
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "codex",
        "PreToolUse",
        {"session_id": "c1", "cwd": str(repo), "tool_name": "apply_patch", "tool_input": {"command": patch}},
    )
    assert code == 0 and err == ""  # a gate-file edit is an at-commit finding
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "codex",
        "PreToolUse",
        {"session_id": "c1", "cwd": str(repo), "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}},
    )
    assert code == 2 and "DECISIONS REQUIRED" in err and "edit core/authz_rules.go" in err
    # gemini: allowed shell command exits 0 silently
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "gemini",
        "BeforeTool",
        {"session_id": "g1", "cwd": str(repo), "tool_name": "run_shell_command", "tool_input": {"command": "ls"}},
    )
    assert (code, out, err) == (0, "", "")
    # cursor: JSON permission, ask for confirm-class, deny for deny-class, allow otherwise
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "cursor",
        "beforeShellExecution",
        {"conversation_id": "cu1", "workspace_roots": [str(repo)], "command": "kubectl apply -f x.yaml"},
    )
    assert code == 0 and json.loads(out)["permission"] == "ask"
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "cursor",
        "beforeShellExecution",
        {"conversation_id": "cu1", "workspace_roots": [str(repo)], "command": "terraform destroy"},
    )
    assert json.loads(out)["permission"] == "deny"
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "cursor",
        "beforeShellExecution",
        {"conversation_id": "cu1", "workspace_roots": [str(repo)], "command": "ls"},
    )
    assert json.loads(out) == {"permission": "allow"}
    # cursor afterFileEdit takes a snapshot and records the blob like Claude Code's PostToolUse
    (repo / "a.txt").write_text("cursor wrote this\n")
    _run_hook(monkeypatch, capsys, "cursor", "sessionStart", {"conversation_id": "cu1", "workspace_roots": [str(repo)]})
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "cursor",
        "afterFileEdit",
        {
            "conversation_id": "cu1",
            "workspace_roots": [str(repo)],
            "file_path": str(repo / "a.txt"),
            "edits": [{"old_string": "a", "new_string": "cursor wrote this"}],
        },
    )
    assert code == 0
    from tests.conftest import git

    assert git(repo, "for-each-ref", "--format=%(refname)", "refs/gitvow/snapshots/").endswith("/cu1/1")
    # an event the adapter does not use is a no-op
    assert _run_hook(monkeypatch, capsys, "gemini", "BeforeModel", {"session_id": "g1", "cwd": str(repo)})[0] == 0


def test_install_per_agent_idempotent_and_reversible(home, repo):
    for agent, rel, key in (
        ("codex", ".codex/hooks.json", "PreToolUse"),
        ("gemini", ".gemini/settings.json", "BeforeTool"),
        ("cursor", ".cursor/hooks.json", "beforeShellExecution"),
    ):
        out = install_user(str(home), agent=agent)
        install_user(str(home), agent=agent)
        s = json.loads((home / rel).read_text())
        assert len(s["hooks"][key]) == 1, agent
        cmd = (
            s["hooks"][key][0]["hooks"][0]["command"]
            if "hooks" in s["hooks"][key][0]
            else s["hooks"][key][0]["command"]
        )
        assert f"hook --agent {agent}" in cmd
        if agent == "codex":
            assert any("hooks = true" in x for x in out)
        if agent == "cursor":
            assert s["version"] == 1 and s["hooks"][key][0]["failClosed"] is True
        if agent == "gemini":
            assert s["hooks"][key][0]["hooks"][0]["name"] == "gitvow-BeforeTool"
        uninstall_user(str(home), agent=agent)
        assert not (home / rel).exists(), agent
    # foreign entries survive
    (home / ".cursor").mkdir(exist_ok=True)
    (home / ".cursor" / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": {"stop": [{"command": "mine.sh"}]}})
    )
    install_user(str(home), agent="cursor")
    uninstall_user(str(home), agent="cursor")
    assert json.loads((home / ".cursor" / "hooks.json").read_text())["hooks"]["stop"] == [{"command": "mine.sh"}]
    # repo-level
    install_repo(str(repo), agent="codex")
    assert (repo / ".codex" / "hooks.json").exists()
    uninstall_repo(str(repo), agent="codex")
    assert not (repo / ".codex" / "hooks.json").exists()


def test_copilot_and_factory_normalization_and_responses():
    c = normalize(
        "copilot",
        "preToolUse",
        {"sessionId": "cp1", "cwd": "/w", "toolName": "bash", "toolArgs": {"command": "rm -rf /"}},
    )
    assert (
        c[0][0] == "PreToolUse"
        and c[0][1]["session_id"] == "cp1"
        and c[0][1]["tool_name"] == "Bash"
        and c[0][1]["tool_input"] == {"command": "rm -rf /"}
    )
    c = normalize(
        "copilot",
        "postToolUse",
        {
            "sessionId": "cp1",
            "cwd": "/w",
            "toolName": "str_replace_editor",
            "toolArgs": {"path": "/w/a.py", "old_str": "x", "new_str": "y"},
        },
    )
    assert c[0][1]["tool_name"] == "Edit" and c[0][1]["tool_input"] == {
        "file_path": "/w/a.py",
        "old_string": "x",
        "new_string": "y",
    }
    c = normalize(
        "copilot",
        "preToolUse",
        {
            "sessionId": "cp1",
            "cwd": "/w",
            "toolName": "create",
            "toolArgs": {"path": "/w/n.py", "file_text": "print(1)"},
        },
    )
    assert c[0][1]["tool_name"] == "Write" and c[0][1]["tool_input"]["content"] == "print(1)"
    assert normalize("copilot", "sessionEnd", {"sessionId": "cp1", "cwd": "/w"})[0][0] == "Stop"
    assert json.loads(respond("copilot", 2, "BLOCKED by policy (x).")[1]) == {
        "permissionDecision": "deny",
        "permissionDecisionReason": "BLOCKED by policy (x).",
    }
    assert json.loads(respond("copilot", 2, "CONFIRMATION REQUIRED (y).")[1])["permissionDecision"] == "ask"
    assert json.loads(respond("copilot", 0, "")[1]) == {"permissionDecision": "allow"}
    f = normalize(
        "factory",
        "PreToolUse",
        {"session_id": "f1", "cwd": "/w", "tool_name": "Execute", "tool_input": {"command": "terraform destroy"}},
    )
    assert f[0][1]["tool_name"] == "Bash"
    f = normalize(
        "factory",
        "PostToolUse",
        {
            "session_id": "f1",
            "cwd": "/w",
            "tool_name": "Create",
            "tool_input": {"file_path": "/w/a.py", "content": "x"},
        },
    )
    assert f[0][1]["tool_name"] == "Write" and f[0][1]["tool_input"]["file_path"] == "/w/a.py"
    f = normalize(
        "factory",
        "PreToolUse",
        {
            "session_id": "f1",
            "cwd": "/w",
            "tool_name": "ApplyPatch",
            "tool_input": {
                "file_path": "/w/a.py",
                "command": '*** Begin Patch\n*** Update File: a.py\n+"/v1/x"\n*** End Patch\n',
            },
        },
    )
    assert f[0][1]["tool_name"] == "Edit" and '"/v1/x"' in f[0][1]["tool_input"]["new_string"]
    assert respond("factory", 2, "BLOCKED")[0] == 2


def test_copilot_factory_cli_and_install(repo, home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "copilot",
        "preToolUse",
        {"sessionId": "cp1", "cwd": str(repo), "toolName": "bash", "toolArgs": {"command": "kubectl apply -f x.yaml"}},
    )
    assert code == 0 and json.loads(out)["permissionDecision"] == "ask"
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "copilot",
        "preToolUse",
        {"sessionId": "cp1", "cwd": str(repo), "toolName": "bash", "toolArgs": {"command": "ls"}},
    )
    assert json.loads(out) == {"permissionDecision": "allow"}
    code, out, err = _run_hook(
        monkeypatch,
        capsys,
        "factory",
        "PreToolUse",
        {"session_id": "f1", "cwd": str(repo), "tool_name": "Execute", "tool_input": {"command": "git push --force"}},
    )
    assert code == 2 and "BLOCKED" in err
    for agent, rel, key in (
        ("copilot", ".copilot/hooks/gitvow.json", "preToolUse"),
        ("factory", ".factory/hooks.json", "PreToolUse"),
    ):
        install_user(str(home), agent=agent)
        install_user(str(home), agent=agent)
        s = json.loads((home / rel).read_text())
        assert len(s["hooks"][key]) == 1
        if agent == "copilot":
            assert s["version"] == 1 and "hook --agent copilot preToolUse" in s["hooks"][key][0]["bash"]
        else:
            assert "hook --agent factory PreToolUse" in s["hooks"][key][0]["hooks"][0]["command"]
        uninstall_user(str(home), agent=agent)
        assert not (home / rel).exists()
    install_repo(str(repo), agent="copilot")
    assert (repo / ".github" / "hooks" / "gitvow.json").exists()
    uninstall_repo(str(repo), agent="copilot")
    assert not (repo / ".github" / "hooks" / "gitvow.json").exists()


def test_cursor_pretooluse_matches_the_documented_payload(repo, home):
    """Cursor sends Write with new_content for file edits and MCP:<tool> for MCP calls."""
    from gitvow.adapters import normalize

    ev, p = normalize(
        "cursor",
        "preToolUse",
        {
            "conversation_id": "cu",
            "workspace_roots": [str(repo)],
            "tool_name": "Write",
            "tool_input": {"file_path": "core/authz_rules.go", "new_content": 'var Public = []string{"/v1/x"}'},
        },
    )[0]
    assert ev == "PreToolUse" and p["tool_name"] == "Write"
    # the gate and the providers read `content`; Cursor's own key is preserved alongside it
    assert p["tool_input"]["content"] == 'var Public = []string{"/v1/x"}'
    assert p["tool_input"]["new_content"] == p["tool_input"]["content"]
    ev, p = normalize(
        "cursor",
        "preToolUse",
        {
            "conversation_id": "cu",
            "workspace_roots": [str(repo)],
            "tool_name": "Delete",
            "tool_input": {"file_path": "core/authz_rules.go"},
        },
    )[0]
    assert p["tool_name"] == "Edit"  # a deleted gate-bearing file still reaches the gate
    for payload, expected in (
        ({"tool_name": "MCP:drop_table", "mcp_server_name": "postgres"}, "mcp__postgres__drop_table"),
        ({"tool_name": "MCP:read_rows"}, "mcp__server__read_rows"),
        ({"tool_name": "mcp__already__mapped"}, "mcp__already__mapped"),
    ):
        ev, p = normalize("cursor", "preToolUse", {"conversation_id": "cu", "workspace_roots": [str(repo)], **payload})[
            0
        ]
        assert p["tool_name"] == expected
