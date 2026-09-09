import json

from gitvow.transcript import summarize

CODEX = [
    {
        "timestamp": "t",
        "type": "session_meta",
        "payload": {"id": "abc", "cwd": "/r", "cli_version": "0.130.0", "git": {"branch": "main"}},
    },
    {"timestamp": "t", "type": "turn_context", "payload": {"cwd": "/r", "model": "gpt-5"}},
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix add"}]},
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "I will fix add() in calc.py; token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 stays out.",
                }
            ],
        },
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": ["git", "status"]}),
            "call_id": "c1",
        },
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {"type": "function_call_output", "call_id": "c1", "output": "clean SECRET=hunter2 (never read)"},
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "apply_patch",
            "input": "*** Begin Patch\n*** Update File: calc.py\n-    return a - b\n+    return a + b\n*** End Patch\n",
            "call_id": "c2",
        },
    },
    {
        "timestamp": "t",
        "type": "event_msg",
        "payload": {"type": "agent_message", "message": "Done: fixed add() in calc.py."},
    },
    {
        "timestamp": "t",
        "type": "event_msg",
        "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 10}}},
    },
]

GEMINI = [
    {"sessionId": "g1", "projectHash": "h", "startTime": "t", "lastUpdated": "t", "kind": "main"},
    {"id": "m1", "timestamp": "t", "type": "user", "content": "fix add"},
    {
        "id": "m2",
        "timestamp": "t",
        "type": "gemini",
        "content": [{"text": "Fixing add() in calc.py now."}],
        "model": "gemini-2.5-pro",
        "toolCalls": [
            {"id": "t1", "name": "run_shell_command", "args": {"command": "git status"}, "status": "success"},
            {
                "id": "t2",
                "name": "replace",
                "args": {"file_path": "/r/calc.py", "old_string": "a - b", "new_string": "a + b"},
                "status": "success",
            },
            {"id": "t3", "name": "write_file", "args": {"file_path": "/r/new.py", "content": "x"}, "status": "success"},
        ],
    },
    {"$set": {"lastUpdated": "t2"}},
    {"id": "m3", "timestamp": "t", "type": "gemini", "content": "All done in calc.py and new.py."},
]


def _write(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


def test_codex_rollout_is_read(tmp_path):
    s = summarize(_write(tmp_path, "rollout.jsonl", CODEX))
    assert s["format"] == "codex" and s["turns"] == 2
    assert [t["tool"] for t in s["tool_calls"]] == ["Bash", "Edit"]
    assert s["tool_calls"][0]["arg"] == "git status" and s["tool_calls"][1]["arg"] == "calc.py"
    assert s["files_written"] == ["calc.py"]
    assert s["last_assistant_text"] == "Done: fixed add() in calc.py."
    assert "hunter2" not in json.dumps(s)  # tool output is never read


def test_gemini_chat_recording_is_read(tmp_path):
    s = summarize(_write(tmp_path, "session-1.jsonl", GEMINI))
    assert s["format"] == "gemini" and s["turns"] == 2
    assert [t["tool"] for t in s["tool_calls"]] == ["Bash", "Edit", "Write"]
    assert sorted(s["files_written"]) == ["/r/calc.py", "/r/new.py"]
    assert s["last_assistant_text"] == "All done in calc.py and new.py."


def test_claude_format_still_default(transcript):
    s = summarize(transcript)
    assert (
        s["format"] == "claude"
        and s["tool_calls"][0]["tool"] == "Bash"
        and "[github-token]" in s["last_assistant_text"]
    )


def test_redaction_applies_to_all_formats(tmp_path):
    s = summarize(_write(tmp_path, "rollout.jsonl", CODEX[:4]))
    assert "[github-token]" in s["last_assistant_text"] and "ghp_" not in s["last_assistant_text"]


# Codex 0.15 shape, captured from a real session: one `exec` custom tool whose input is a JavaScript
# snippet calling tools.exec_command / tools.apply_patch, and user text as input_text parts.
CODEX_WRAPPED = [
    {"timestamp": "t", "type": "session_meta", "payload": {"id": "c2", "cwd": "/r"}},
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "<recommended_plugins>\nAirtable\n</recommended_plugins>"}],
        },
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix add"}]},
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "input": 'const r = await tools.exec_command({"cmd":"git status","workdir":"/r"});\nconsole.log(r);',
        },
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {
            "type": "custom_tool_call",
            "name": "exec",
            "input": 'await tools.apply_patch("*** Begin Patch\\n*** Update File: /r/calc.py\\n+a + b\\n*** End Patch\\n");',
        },
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Fixed."}]},
    },
    {
        "timestamp": "t",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "accept it"}]},
    },
]


def test_codex_wrapped_tool_calls_and_user_turns(tmp_path):
    s = summarize(_write(tmp_path, "rollout.jsonl", CODEX_WRAPPED))
    assert s["format"] == "codex"
    assert [t["tool"] for t in s["tool_calls"]] == ["Bash", "Edit"]
    assert s["tool_calls"][0]["arg"] == "git status" and s["tool_calls"][1]["arg"] == "/r/calc.py"
    assert s["files_written"] == ["/r/calc.py"]
    # the injected <recommended_plugins> block is not a person speaking; the two real prompts are
    assert s["user_turns"] == 2
