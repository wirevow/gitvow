import json
import sys
from pathlib import Path

from gitvow import cli
from gitvow.hooks import pre_tool_use
from gitvow.policy import evaluate
from gitvow.providers import questions_for, route_literals

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "providers" / "static_facts.py"


def test_route_literals_and_questions():
    txt = 'a = "/v1/orders"; b = \'/v1/orders/{id}\'; c = "/static/app.js"; d = "//comment"; e = "/"; f = "/v1/orders"'
    assert route_literals(txt) == ["/v1/orders", "/v1/orders/{id}"]
    qs = questions_for(
        "Edit",
        {"file_path": "api.py", "old_string": '@app.get("/old")', "new_string": '@app.get("/new")\n@app.get("/old2")'},
    )
    assert qs == [
        ("gate_bearing", "api.py"),
        ("route_gate", "/new"),
        ("route_gate", "/old2"),
        ("route_callers", "/old"),
    ]
    assert questions_for("Write", {"file_path": "x.py", "content": 'r("/w")'}) == [
        ("gate_bearing", "x.py"),
        ("route_gate", "/w"),
    ]
    assert questions_for("Bash", {"command": 'echo "/v1/x"'}) == []


def _facts(tmp_path):
    f = tmp_path / "facts.json"
    f.write_text(
        json.dumps(
            {
                "gate_bearing": ["auth/AuthorizeWhitelistedPaths.java", "deploy/values/production-*.yaml"],
                "whitelist_file": "auth/AuthorizeWhitelistedPaths.java",
                "authorized_routes": ["/v1/orders", "/v1/orders/{id}"],
                "callers": {"/v1/orders": ["client-orch (OrdersClient.java:88)"]},
            }
        )
    )
    return f


def _policy(tmp_path, command):
    return {"providers": [{"name": "facts", "command": command}]}


def test_provider_confirms_with_evidence(tmp_path):
    pol = _policy(tmp_path, f"{sys.executable} {EXAMPLE} --facts {_facts(tmp_path)}")
    d = evaluate(
        pol,
        "Edit",
        {"file_path": "src/api/Orders.java", "old_string": "x", "new_string": '@Path("/v1/orders/export")'},
        str(tmp_path),
    )
    assert d.outcome == "confirm" and "not covered by any authorization rule" in d.reason
    assert "AuthorizeWhitelistedPaths.java" in d.reason
    d = evaluate(
        pol,
        "Edit",
        {"file_path": "src/api/Orders.java", "old_string": "x", "new_string": '@Path("/v1/orders/{id}")'},
        str(tmp_path),
    )
    assert d.outcome == "allow"  # listed as authorized
    d = evaluate(
        pol,
        "Edit",
        {"file_path": "src/api/Orders.java", "old_string": '@Path("/v1/orders")', "new_string": ""},
        str(tmp_path),
    )
    assert d.outcome == "confirm" and "client-orch" in d.reason
    d = evaluate(
        pol,
        "Write",
        {"file_path": str(tmp_path / "deploy/values/production-in.yaml"), "content": "a: 1"},
        str(tmp_path),
    )
    assert d.outcome == "confirm" and "gate-bearing" in d.reason
    assert (
        evaluate(pol, "Edit", {"file_path": "README.md", "old_string": "a", "new_string": "b"}, str(tmp_path)).outcome
        == "allow"
    )
    assert evaluate(pol, "Bash", {"command": 'echo "/v1/new"'}, str(tmp_path)).outcome == "allow"


def test_provider_failure_means_confirm(tmp_path):
    for cmd in (
        "/nonexistent/provider",
        f"{sys.executable} -c \"print('not json')\"",
        f"{sys.executable} -c 'import sys; sys.exit(3)'",
        "",
    ):
        pol = _policy(tmp_path, cmd)
        d = evaluate(pol, "Edit", {"file_path": "a.py", "old_string": "", "new_string": ""}, str(tmp_path))
        assert d.outcome == "confirm" and "unavailable" in d.reason, cmd
    pol = _policy(tmp_path, f"{sys.executable} -c 'import time; time.sleep(5)'")
    pol["providers"][0]["timeout"] = 0.5
    assert evaluate(pol, "Edit", {"file_path": "a.py"}, str(tmp_path)).outcome == "confirm"


def test_unknown_answer_does_not_block(tmp_path):
    pol = _policy(tmp_path, f'{sys.executable} -c "print(\'{{\\"answer\\": \\"unknown\\"}}\')"')
    assert evaluate(pol, "Edit", {"file_path": "a.py"}, str(tmp_path)).outcome == "allow"


def test_provider_questions_filter_and_validation(tmp_path):
    from gitvow.policy import PolicyError, load_policy

    (tmp_path / ".gitvow").mkdir()
    (tmp_path / ".gitvow" / "policy.json").write_text(
        json.dumps({"providers": [{"name": "x", "command": "y", "questions": ["nope"]}]})
    )
    try:
        load_policy(str(tmp_path), str(tmp_path / "h"))
        raise AssertionError("expected PolicyError")
    except PolicyError as e:
        assert "unknown questions" in str(e)
    (tmp_path / ".gitvow" / "policy.json").write_text(json.dumps({"providers": [{"name": "x"}]}))
    try:
        load_policy(str(tmp_path), str(tmp_path / "h"))
        raise AssertionError("expected PolicyError")
    except PolicyError:
        pass
    pol = _policy(tmp_path, f"{sys.executable} {EXAMPLE} --facts {_facts(tmp_path)}")
    pol["providers"][0]["questions"] = ["gate_bearing"]  # route questions not asked of this provider
    d = evaluate(pol, "Edit", {"file_path": "a.py", "old_string": "", "new_string": '"/v1/zzz"'}, str(tmp_path))
    assert d.outcome == "allow"


def test_hook_and_cli_ask_end_to_end(repo, home, payload, tmp_path, monkeypatch, capsys):
    facts = _facts(tmp_path)
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "policy.json").write_text(
        json.dumps({"providers": [{"name": "facts", "command": f"{sys.executable} {EXAMPLE} --facts {facts}"}]})
    )
    code, msg = pre_tool_use(
        payload(
            "PreToolUse",
            "Edit",
            {"file_path": str(repo / "src/Orders.java"), "old_string": "", "new_string": '@Path("/v1/orders/export")'},
        ),
        str(home),
    )
    assert code == 0 and msg == ""  # a provider yes is an at-commit finding: the agent keeps working
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "finding"' in log and "provider facts" in log
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert code == 2 and "DECISIONS REQUIRED" in msg and "route /v1/orders/export in src/Orders.java" in msg
    assert "AuthorizeWhitelistedPaths.java" in msg  # the provider's evidence travels to the card
    monkeypatch.chdir(repo)
    assert cli.main(["ask", "route_gate", "/v1/orders/export", "--path", "src/Orders.java"]) == 0
    out = capsys.readouterr().out
    assert "facts: yes" in out and "decision: CONFIRM AT COMMIT" in out
    assert cli.main(["ask", "route_gate", "/v1/orders"]) == 0
    assert "decision: ALLOW" in capsys.readouterr().out
    assert cli.main(["ask", "gate_bearing", "auth/AuthorizeWhitelistedPaths.java"]) == 0
    (repo / ".gitvow" / "policy.json").write_text("{}")
    assert cli.main(["ask", "gate_bearing", "x"]) == 1
