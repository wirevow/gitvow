"""Adapters: translate other agents' hook payloads into the Claude Code shape gitvow's handlers understand,
and render decisions in each agent's native form. See docs/reference/adapters.md."""

from __future__ import annotations

import json
import re
from typing import Any

AGENTS = ("claude", "codex", "gemini", "cursor")

# agent event -> gitvow handler
EVENT_MAP: dict[str, dict[str, str]] = {
    "claude": {
        "SessionStart": "SessionStart",
        "PreToolUse": "PreToolUse",
        "PostToolUse": "PostToolUse",
        "Stop": "Stop",
    },
    "codex": {"SessionStart": "SessionStart", "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse", "Stop": "Stop"},
    "gemini": {
        "SessionStart": "SessionStart",
        "BeforeTool": "PreToolUse",
        "AfterTool": "PostToolUse",
        "SessionEnd": "Stop",
    },
    "cursor": {
        "sessionStart": "SessionStart",
        "preToolUse": "PreToolUse",
        "beforeShellExecution": "PreToolUse",
        "beforeMCPExecution": "PreToolUse",
        "afterFileEdit": "PostToolUse",
        "afterShellExecution": "PostToolUse",
        "stop": "Stop",
    },
}

GEMINI_TOOLS = {"run_shell_command": "Bash", "write_file": "Write", "replace": "Edit", "edit": "Edit"}
PATCH_FILE_RE = re.compile(r"^\*\*\* (Update|Add|Delete) File: (.+)$")


def parse_apply_patch(text: str) -> list[dict[str, str]]:
    """Codex apply_patch text -> [{file_path, old_string, new_string, op}] per touched file."""
    files: list[dict[str, str]] = []
    cur: dict[str, Any] | None = None
    for line in (text or "").splitlines():
        m = PATCH_FILE_RE.match(line.strip())
        if m:
            cur = {"op": m.group(1).lower(), "file_path": m.group(2).strip(), "old": [], "new": []}
            files.append(cur)
            continue
        if cur is None or line.startswith("*** "):
            continue
        if line.startswith("+"):
            cur["new"].append(line[1:])
        elif line.startswith("-"):
            cur["old"].append(line[1:])
    return [
        {
            "file_path": f["file_path"],
            "op": f["op"],
            "old_string": "\n".join(f["old"]),
            "new_string": "\n".join(f["new"]),
        }
        for f in files
    ]


def normalize(agent: str, event: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return one or more (gitvow_event, claude_shaped_payload). Codex patches expand to one Edit per file."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent}")
    gv_event = EVENT_MAP[agent].get(event)
    if not gv_event:
        return []
    base = {
        "session_id": payload.get("session_id") or payload.get("conversation_id") or "",
        "transcript_path": payload.get("transcript_path") or "",
        "cwd": payload.get("cwd") or (payload.get("workspace_roots") or [None])[0] or "",
        "hook_event_name": gv_event,
        "agent": agent,
    }
    if gv_event in ("SessionStart", "Stop"):
        return [(gv_event, base)]
    tool = str(payload.get("tool_name") or "")
    inp = dict(payload.get("tool_input") or {})
    if agent == "codex":
        if tool == "apply_patch":
            out = []
            for f in parse_apply_patch(str(inp.get("command") or inp.get("patch") or "")):
                out.append(
                    (
                        gv_event,
                        {
                            **base,
                            "tool_name": "Edit",
                            "tool_input": {
                                "file_path": f["file_path"],
                                "old_string": f["old_string"],
                                "new_string": f["new_string"],
                                "apply_patch_op": f["op"],
                            },
                        },
                    )
                )
            return out or [(gv_event, {**base, "tool_name": "Edit", "tool_input": {}})]
        return [(gv_event, {**base, "tool_name": tool, "tool_input": inp})]
    if agent == "gemini":
        return [(gv_event, {**base, "tool_name": GEMINI_TOOLS.get(tool, tool), "tool_input": inp})]
    if agent == "cursor":
        if event == "beforeShellExecution" or event == "afterShellExecution":
            return [(gv_event, {**base, "tool_name": "Bash", "tool_input": {"command": payload.get("command", "")}})]
        if event == "afterFileEdit":
            edits = payload.get("edits") or []
            return [
                (
                    gv_event,
                    {
                        **base,
                        "tool_name": "Edit",
                        "tool_input": {
                            "file_path": payload.get("file_path", ""),
                            "old_string": "\n".join(str(e.get("old_string", "")) for e in edits),
                            "new_string": "\n".join(str(e.get("new_string", "")) for e in edits),
                        },
                    },
                )
            ]
        if event == "beforeMCPExecution":
            name = tool if tool.startswith("mcp__") else f"mcp__{payload.get('mcp_server_name', 'server')}__{tool}"
            return [(gv_event, {**base, "tool_name": name, "tool_input": inp})]
        # generic preToolUse: Cursor's own tool names
        mapped = {
            "Shell": "Bash",
            "shell": "Bash",
            "run_terminal_cmd": "Bash",
            "edit_file": "Edit",
            "write_file": "Write",
            "Edit": "Edit",
            "Write": "Write",
        }.get(tool, tool)
        if mapped == "Bash" and "command" not in inp and payload.get("command"):
            inp["command"] = payload["command"]
        return [(gv_event, {**base, "tool_name": mapped, "tool_input": inp})]
    return [(gv_event, {**base, "tool_name": tool, "tool_input": inp})]


def respond(agent: str, code: int, msg: str) -> tuple[int, str, str]:
    """(exit code, stdout, stderr) in the agent's form. gitvow's code 2 means blocked; the message says DENY or CONFIRM."""
    if agent == "cursor":
        if code == 2:
            perm = "deny" if msg.startswith("BLOCKED") else "ask"
            return 0, json.dumps({"permission": perm, "user_message": msg, "agent_message": msg}), ""
        return 0, json.dumps({"permission": "allow"}), msg
    return code, "", msg
