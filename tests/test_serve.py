"""0.28: the record server. Read-only MCP over stdio; coverage on every answer; quoted content marked as data."""

import io
import json
import os
import subprocess
import sys

from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.serve import DATA_HEAD, call_tool, config_snippets, coverage, handle, serve
from tests.conftest import git


def _decided_repo(repo, home, payload, transcript):
    """A repository with one accepted decision on a commit, so standing and the brief have something to say."""
    from gitvow import decisions as dec
    from gitvow.policy import load_policy

    session_start(payload("SessionStart"), str(home))
    (repo / "values" / "production-in").mkdir(parents=True)
    target = repo / "values" / "production-in" / "app.yaml"
    target.write_text("replicas: 1\n")
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(target)}), str(home))
    pol = load_policy(str(repo), str(home))
    dec.decide(str(repo), "1", "accept", pol, reason="reviewed", rules=[])
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "prod values")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert "Gitvow-Accepted" in git(repo, "log", "-1", "--format=%B")
    return repo


def test_handshake_and_tool_list(repo):
    init = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, str(repo)
    )
    assert init["result"]["serverInfo"]["name"] == "gitvow" and "read-only" in init["result"]["instructions"]
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, str(repo)) is None
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, str(repo))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"record_brief", "record_claims", "record_standing", "record_why", "record_check", "record_status"} <= names
    assert all(t["description"].endswith("Read-only.") for t in tools)
    assert all("repo" in t["inputSchema"]["properties"] for t in tools)
    assert handle({"jsonrpc": "2.0", "id": 3, "method": "nope"}, str(repo))["error"]["code"] == -32601


def test_coverage_is_none_on_an_empty_repository_and_full_after_a_decision(repo, home, payload, transcript):
    assert coverage(str(repo), str(home))["level"] == "none"
    _decided_repo(repo, home, payload, transcript)
    cov = coverage(str(repo), str(home))
    assert cov["level"] == "full" and cov["decisions"] == 1 and cov["session_notes"] == 1 and cov["hooks_have_run_here"]


def test_standing_and_brief_carry_the_decision_and_mark_quotes_as_data(repo, home, payload, transcript):
    _decided_repo(repo, home, payload, transcript)
    st = call_tool("record_standing", {}, str(repo), str(home))
    assert st["coverage"]["level"] == "full" and st["data"]["decisions"][0]["answer"] == "accepted"
    assert st["text"].startswith(DATA_HEAD) and "coverage: full" in st["text"]
    br = call_tool("record_brief", {}, str(repo), str(home))
    assert br["data"]["source"] == "repo" and DATA_HEAD in br["text"]
    why = call_tool("record_why", {"path": "values/production-in/app.yaml"}, str(repo), str(home))
    assert "values/production-in/app.yaml" in why["text"]


def test_check_is_a_dry_run_that_records_nothing(repo, home):
    before = (repo / ".git" / "gitvow-hooks.log").read_text() if (repo / ".git" / "gitvow-hooks.log").exists() else ""
    res = call_tool("record_check", {"command": "git push origin main"}, str(repo), str(home))
    assert res["data"]["verdict"] == "confirm" and "nothing was recorded" in res["text"]
    after = (repo / ".git" / "gitvow-hooks.log").read_text() if (repo / ".git" / "gitvow-hooks.log").exists() else ""
    assert before == after and not (repo / ".git" / "gitvow-session.json").exists()


def test_tool_errors_are_results_not_crashes(repo, home, tmp_path):
    r = handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "record_why", "arguments": {}}},
        str(repo),
        str(home),
    )
    assert r["result"]["isError"] and "path is required" in r["result"]["content"][0]["text"]
    r = handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "record_brief", "arguments": {"repo": str(tmp_path)}},
        },
        str(repo),
        str(home),
    )
    assert r["result"]["isError"] and "not inside a git repository" in r["result"]["content"][0]["text"]
    r = handle(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "record_delete"}}, str(repo), str(home)
    )
    assert r["result"]["isError"]


def test_stdio_loop_speaks_one_json_per_line(repo, home):
    inp = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n\n"
        + "not json\n"
        + json.dumps(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "record_status", "arguments": {}}}
        )
        + "\n"
    )
    out = io.StringIO()
    assert serve(str(repo), str(home), stdin=inp, stdout=out) == 0
    lines = [json.loads(ln) for ln in out.getvalue().splitlines()]
    assert lines[0]["result"]["serverInfo"]["name"] == "gitvow"
    assert lines[1]["error"]["code"] == -32700
    assert lines[2]["result"]["structuredContent"]["coverage"]["level"] == "none"
    assert "[gitvow" in lines[2]["result"]["content"][0]["text"]


def test_cli_serve_subprocess_and_print_config(repo):
    env = {**os.environ, "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")}
    msgs = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    r = subprocess.run(
        [sys.executable, "-m", "gitvow", "serve", "--repo", str(repo)],
        input=msgs,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert r.returncode == 0 and json.loads(r.stdout.splitlines()[0])["result"]["tools"]
    snippet = config_snippets(str(repo))
    assert "claude mcp add gitvow" in snippet and "mcp_servers.gitvow" in snippet
    r = subprocess.run(
        [sys.executable, "-m", "gitvow", "serve", "--repo", str(repo), "--print-config"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert "read-only" in r.stdout
