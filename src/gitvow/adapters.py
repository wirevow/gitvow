"""Adapters: translate other agents' hook payloads into the Claude Code shape gitvow's handlers understand,
and render decisions in each agent's native form. See docs/reference/adapters.md."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

AGENTS = ("claude", "codex", "gemini", "cursor", "copilot", "factory")

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
    "copilot": {
        "sessionStart": "SessionStart",
        "preToolUse": "PreToolUse",
        "postToolUse": "PostToolUse",
        "sessionEnd": "Stop",
    },
    "factory": {
        "SessionStart": "SessionStart",
        "PreToolUse": "PreToolUse",
        "PostToolUse": "PostToolUse",
        "Stop": "Stop",
        "SessionEnd": "Stop",
    },
    "cursor": {
        "sessionStart": "SessionStart",
        "preToolUse": "PreToolUse",
        "beforeShellExecution": "PreToolUse",
        "beforeMCPExecution": "PreToolUse",
        "afterFileEdit": "PostToolUse",
        "afterShellExecution": "PostToolUse",
        "subagentStop": "Subagent",
        "stop": "Stop",
    },
}

GEMINI_TOOLS = {"run_shell_command": "Bash", "write_file": "Write", "replace": "Edit", "edit": "Edit"}
COPILOT_TOOLS = {
    "bash": "Bash",
    "powershell": "Bash",
    "edit": "Edit",
    "str_replace_editor": "Edit",
    "apply_patch": "Edit",
    "create": "Write",
}
FACTORY_TOOLS = {"Execute": "Bash", "Edit": "Edit", "ApplyPatch": "Edit", "Create": "Write", "MultiEdit": "MultiEdit"}


def _edit_input(args: dict[str, Any]) -> dict[str, Any]:
    """Common edit argument names across agents -> Claude Code Edit/Write input."""
    out: dict[str, Any] = {}
    fp = args.get("file_path") or args.get("path") or args.get("filePath") or ""
    if fp:
        out["file_path"] = str(fp)
    for src, dst in (
        ("old_string", "old_string"),
        ("old_str", "old_string"),
        ("new_string", "new_string"),
        ("new_str", "new_string"),
        ("content", "content"),
        ("file_text", "content"),
    ):
        if src in args and dst not in out:
            out[dst] = args[src]
    if "command" in args and isinstance(args["command"], str) and "*** Begin Patch" in args["command"]:
        files = parse_apply_patch(args["command"])
        if files:
            out.setdefault("file_path", files[0]["file_path"])
            out.setdefault("old_string", files[0]["old_string"])
            out.setdefault("new_string", files[0]["new_string"])
    return out


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
        "session_id": payload.get("session_id") or payload.get("conversation_id") or payload.get("sessionId") or "",
        "transcript_path": payload.get("transcript_path") or "",
        "cwd": payload.get("cwd") or (payload.get("workspace_roots") or [None])[0] or "",
        "hook_event_name": gv_event,
        "agent": agent,
    }
    if gv_event in ("SessionStart", "Stop"):
        return [(gv_event, base)]
    if gv_event == "Subagent":
        return [
            (
                gv_event,
                {
                    **base,
                    "status": payload.get("status", ""),
                    "subagent_type": payload.get("subagent_type", ""),
                    "tool_call_count": payload.get("tool_call_count", 0),
                },
            )
        ]
    tool = str(payload.get("tool_name") or payload.get("toolName") or "")
    raw_in = payload.get("tool_input")
    if raw_in is None:
        raw_in = payload.get("toolArgs")
    inp = dict(raw_in) if isinstance(raw_in, dict) else ({"command": raw_in} if isinstance(raw_in, str) else {})
    if agent == "copilot":
        mapped = COPILOT_TOOLS.get(tool, tool)
        if mapped in ("Edit", "Write"):
            return [(gv_event, {**base, "tool_name": mapped, "tool_input": _edit_input(inp)})]
        if mapped == "Bash":
            return [
                (
                    gv_event,
                    {
                        **base,
                        "tool_name": "Bash",
                        "tool_input": {"command": str(inp.get("command") or inp.get("cmd") or "")},
                    },
                )
            ]
        return [(gv_event, {**base, "tool_name": mapped, "tool_input": inp})]
    if agent == "factory":
        mapped = FACTORY_TOOLS.get(tool, tool)
        if mapped in ("Edit", "Write", "MultiEdit"):
            return [(gv_event, {**base, "tool_name": mapped, "tool_input": {**inp, **_edit_input(inp)}})]
        return [(gv_event, {**base, "tool_name": mapped, "tool_input": inp})]
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
    if agent == "copilot":
        if code == 2:
            perm = "deny" if msg.startswith("BLOCKED") else "ask"
            return 0, json.dumps({"permissionDecision": perm, "permissionDecisionReason": msg}), ""
        return 0, json.dumps({"permissionDecision": "allow"}), msg
    return code, "", msg


# ---------------------------------------------------------------------------
# External adapters: gitvow-agent-<name> executables (see docs/reference/adapter-protocol.md)
# ---------------------------------------------------------------------------


class AdapterError(Exception):
    """The external adapter is missing, failed, or returned malformed JSON."""


def external_path(agent: str, home: str | None = None) -> str | None:
    if agent in AGENTS or not re.fullmatch(r"[A-Za-z0-9_-]+", agent or ""):
        return None
    name = f"gitvow-agent-{agent}"
    found = shutil.which(name)
    if found:
        return found
    cand = os.path.join(home or os.path.expanduser("~"), ".gitvow", "agents", name)
    return cand if os.access(cand, os.X_OK) else None


def _run_external(exe: str, sub: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    try:
        r = subprocess.run(
            [exe, sub],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise AdapterError(f"{os.path.basename(exe)} {sub}: {type(e).__name__}") from e
    if r.returncode != 0:
        raise AdapterError(f"{os.path.basename(exe)} {sub}: exit {r.returncode} {r.stderr.strip()[:120]}")
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        raise AdapterError(f"{os.path.basename(exe)} {sub}: malformed JSON") from e
    if not isinstance(out, dict):
        raise AdapterError(f"{os.path.basename(exe)} {sub}: expected an object")
    return out


def external_info(exe: str) -> dict[str, Any]:
    info = _run_external(exe, "info", None)
    if info.get("protocol") != 1:
        raise AdapterError(f"{os.path.basename(exe)}: unsupported protocol {info.get('protocol')!r}")
    return info


def external_normalize(exe: str, event: str, payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = _run_external(exe, "normalize", {"event": event, "payload": payload})
    calls = out.get("calls")
    if not isinstance(calls, list):
        raise AdapterError(f"{os.path.basename(exe)} normalize: 'calls' must be a list")
    result: list[tuple[str, dict[str, Any]]] = []
    for c in calls:
        ev, p = (c or {}).get("event"), (c or {}).get("payload")
        if ev not in ("SessionStart", "PreToolUse", "PostToolUse", "Stop", "Subagent") or not isinstance(p, dict):
            raise AdapterError(f"{os.path.basename(exe)} normalize: bad call {c!r}"[:160])
        p.setdefault("hook_event_name", ev)
        result.append((ev, p))
    return result


def external_respond(exe: str, code: int, msg: str) -> tuple[int, str, str]:
    out = _run_external(exe, "respond", {"code": code, "message": msg})
    return int(out.get("exit_code", code)), str(out.get("stdout", "")), str(out.get("stderr", ""))
