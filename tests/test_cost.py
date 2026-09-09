import json

from gitvow import cli
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from gitvow.pricing import DEFAULTS, estimate, price_for, table
from gitvow.transcript import summarize
from tests.conftest import git


def test_pricing_table_and_prefix_match():
    tbl, label = table(None)
    assert tbl is DEFAULTS and label.startswith("gitvow defaults")
    assert price_for("claude-fable-5-1", tbl)["output"] == 75
    assert price_for("claude-fable-5-1-20261001", tbl)["input"] == 15  # prefix
    assert price_for("gpt-5-mini", tbl) is not None and price_for("totally-unknown", tbl) is None
    tbl2, label2 = table({"pricing": {"my-model": {"input": 1, "output": 2}, "gpt-5": {"input": 0, "output": 0}}})
    assert "overrides" in label2 and tbl2["my-model"] == {"input": 1.0, "output": 2.0} and tbl2["gpt-5"]["input"] == 0


def test_estimate_math_and_unpriced():
    usage = {
        "by_model": {
            "claude-sonnet-5": {
                "input": 1_000_000,
                "output": 100_000,
                "cache_read": 2_000_000,
                "cache_write": 0,
                "reasoning": 0,
            },
            "mystery": {"input": 5, "output": 5, "cache_read": 0, "cache_write": 0, "reasoning": 0},
        }
    }
    e = estimate(usage, None)
    assert e["estimated_cost_usd"] == round(3 + 1.5 + 0.6, 4) and e["unpriced_models"] == ["mystery"]


def _claude_transcript(tmp_path):
    rows = []
    for i, (model, inp, out, cr, cw) in enumerate(
        [("claude-fable-5-1", 1000, 200, 5000, 100), ("claude-fable-5-1", 1200, 300, 6000, 0)]
    ):
        rows.append(
            {
                "type": "assistant",
                "requestId": f"req-{i}",
                "message": {
                    "role": "assistant",
                    "model": model,
                    "usage": {
                        "input_tokens": inp,
                        "output_tokens": out,
                        "cache_read_input_tokens": cr,
                        "cache_creation_input_tokens": cw,
                    },
                    "content": [{"type": "text", "text": f"step {i}"}],
                },
            }
        )
        # a streamed duplicate of the same request must not double count
        rows.append(
            {
                "type": "assistant",
                "requestId": f"req-{i}",
                "message": {
                    "role": "assistant",
                    "model": model,
                    "usage": {
                        "input_tokens": inp,
                        "output_tokens": out,
                        "cache_read_input_tokens": cr,
                        "cache_creation_input_tokens": cw,
                    },
                    "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
                },
            }
        )
    # a subagent side conversation with two tool calls
    rows.append(
        {
            "type": "assistant",
            "isSidechain": True,
            "agentId": "sub-1",
            "requestId": "req-s",
            "message": {
                "role": "assistant",
                "model": "claude-haiku-4-5",
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {}},
                    {"type": "tool_use", "name": "Grep", "input": {}},
                ],
            },
        }
    )
    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def test_claude_usage_dedup_and_subagents(tmp_path):
    s = summarize(_claude_transcript(tmp_path))
    u = s["usage"]
    assert (
        u["input_tokens"] == 2210
        and u["output_tokens"] == 505
        and u["cache_read_tokens"] == 11000
        and u["cache_write_tokens"] == 100
    )
    assert (
        u["models"] == {"claude-fable-5-1": 2, "claude-haiku-4-5": 1} and u["total_tokens"] == 2210 + 505 + 11000 + 100
    )
    assert (
        s["subagents"] == {"count": 1, "tool_calls": 2} and s["turns"] == 4
    )  # side conversation not counted as a turn
    e = estimate(u, None)
    assert e["estimated_cost_usd"] > 0 and "unpriced_models" not in e


def test_codex_and_gemini_usage(tmp_path):
    codex = [
        {"type": "session_meta", "payload": {"id": "x"}},
        {"type": "turn_context", "payload": {"model": "gpt-5"}},
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 400,
                        "output_tokens": 50,
                        "reasoning_output_tokens": 20,
                    }
                },
            },
        },
        {
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 3000,
                        "cached_input_tokens": 1000,
                        "output_tokens": 150,
                        "reasoning_output_tokens": 60,
                    }
                },
            },
        },
    ]
    p = tmp_path / "r.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in codex) + "\n")
    u = summarize(str(p))["usage"]
    assert (u["input_tokens"], u["cache_read_tokens"], u["output_tokens"], u["reasoning_tokens"]) == (
        2000,
        1000,
        150,
        60,
    )  # last running total wins
    assert list(u["models"]) == ["gpt-5"]
    gem = [
        {"sessionId": "g", "projectHash": "h"},
        {
            "type": "gemini",
            "content": "hi",
            "model": "gemini-2.5-pro",
            "tokens": {"input": 500, "output": 40, "cached": 100, "thoughts": 30, "tool": 10},
        },
    ]
    p2 = tmp_path / "g.jsonl"
    p2.write_text("\n".join(json.dumps(r) for r in gem) + "\n")
    u2 = summarize(str(p2))["usage"]
    assert (u2["input_tokens"], u2["output_tokens"], u2["cache_read_tokens"], u2["reasoning_tokens"]) == (
        500,
        50,
        100,
        30,
    )


def test_usage_flows_to_note_ledger_report_digest_handoff(repo, home, payload, tmp_path, monkeypatch, capsys):
    t = _claude_transcript(tmp_path)
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "policy.json").write_text(
        json.dumps({"pricing": {"claude-fable-5-1": {"input": 10, "output": 100, "cache_read": 1, "cache_write": 10}}})
    )
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    base = git(repo, "rev-parse", "HEAD")
    p = payload("SessionStart", transcript=t)
    session_start(p, str(home))
    (repo / "a.txt").write_text("x\n")
    pre_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))
    git(repo, "commit", "-qam", "agent")
    post_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))
    stop(p, str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 4 and note["usage"]["total_tokens"] == 13815 and note["subagents"]["count"] == 1
    assert "overrides" in note["usage"]["pricing"] and note["usage"]["estimated_cost_usd"] > 0
    led = json.loads((home / ".gitvow" / "ledger" / "sess-1.json").read_text())
    assert led["usage"]["total_tokens"] == 13815
    monkeypatch.chdir(repo)
    assert cli.main(["report", "--base", base]) == 0
    out = capsys.readouterr().out
    assert "tokens so far" in out and "est." in out
    assert cli.main(["digest", "--since", "30d"]) == 0
    out = capsys.readouterr().out
    assert "Cost: $" in out and "estimated" in out
    assert cli.main(["handoff"]) == 0
    assert "Tokens so far: 14k" in capsys.readouterr().out


def test_cursor_subagent_event_logged(repo, home, monkeypatch, capsys):
    import io

    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {
                    "conversation_id": "cu1",
                    "workspace_roots": [str(repo)],
                    "status": "completed",
                    "subagent_type": "explore",
                    "tool_call_count": 7,
                }
            )
        ),
    )
    assert cli.main(["hook", "--agent", "cursor", "subagentStop"]) == 0
    assert '"kind": "subagent"' in (repo / ".git" / "gitvow-hooks.log").read_text()
