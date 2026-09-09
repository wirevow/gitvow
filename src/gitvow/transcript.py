"""Structural, redacted summary of an agent transcript (Claude Code JSONL). Tool output is never read."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

from .redact import redact


def _detect(first: dict[str, Any]) -> str:
    """Which agent wrote this transcript: claude (default), codex (rollout records), gemini (chat recording)."""
    t = first.get("type")
    if t in ("session_meta", "response_item", "event_msg", "turn_context", "compacted") and "payload" in first:
        return "codex"
    if "sessionId" in first and "projectHash" in first:
        return "gemini"
    if first.get("type") in ("user", "gemini", "info", "error") and "content" in first and "message" not in first:
        return "gemini"
    return "claude"


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") in ("text", "output_text", "input_text") and c.get("text"):
                    parts.append(str(c["text"]))
                elif "text" in c and isinstance(c["text"], str):
                    parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return "\n".join(parts)
    return ""


def _patch_files(text: str) -> list[str]:
    import re

    return re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", text or "", re.M)


def _codex_tool(name: str, arguments: Any) -> tuple[str, str, list[str]]:
    """Codex function names -> (gitvow tool, brief argument, files written)."""
    args: dict[str, Any] = {}
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip().startswith("{") else {"input": arguments}
        except json.JSONDecodeError:
            args = {"input": arguments}
    elif isinstance(arguments, dict):
        args = arguments
    if name in ("exec_command", "shell", "container.exec", "local_shell"):
        cmd = args.get("cmd") or args.get("command") or ""
        cmd = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        return "Bash", cmd, []
    if name == "apply_patch":
        patch = str(args.get("input") or args.get("patch") or args.get("command") or "")
        files = _patch_files(patch)
        return "Edit", files[0] if files else "apply_patch", files
    return name, json.dumps(args)[:160] if args else "", []


def _gemini_tool(name: str, args: Any) -> tuple[str, str, list[str]]:
    a = args if isinstance(args, dict) else {}
    if name == "run_shell_command":
        return "Bash", str(a.get("command") or ""), []
    if name in ("write_file", "replace", "edit"):
        fp = str(a.get("file_path") or a.get("path") or "")
        return ("Write" if name == "write_file" else "Edit"), fp, [fp] if fp else []
    return name, json.dumps(a)[:160] if a else "", []


def summarize(path: str | None, max_tools: int = 500, rules: Iterable[tuple[str, str]] = ()) -> dict[str, Any]:
    """Structural summary of a transcript. Reads Claude Code, Codex and Gemini CLI formats; tool output is never read."""
    out: dict[str, Any] = {
        "turns": 0,
        "tool_calls": [],
        "last_assistant_text": "",
        "files_written": [],
        "format": "claude",
    }
    if not path or not os.path.exists(path):
        return out
    rules = list(rules)
    written: set[str] = set()
    fmt: str | None = None
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if fmt is None:
                fmt = _detect(ev)
                out["format"] = fmt
            if fmt == "codex":
                _codex_event(ev, out, written, rules, max_tools)
            elif fmt == "gemini":
                _gemini_event(ev, out, written, rules, max_tools)
            else:
                _claude_event(ev, out, written, rules, max_tools)
    out["files_written"] = sorted(written)
    return out


def _add_tool(out: dict[str, Any], tool: str, brief: str, rules: list[tuple[str, str]], max_tools: int) -> None:
    if len(out["tool_calls"]) < max_tools:
        out["tool_calls"].append({"tool": tool, "arg": redact(str(brief), rules)[:160]})


def _claude_event(
    ev: dict[str, Any], out: dict[str, Any], written: set[str], rules: list[tuple[str, str]], max_tools: int
) -> None:
    msg = ev.get("message") or {}
    role = msg.get("role") or ev.get("type")
    content = msg.get("content")
    if role != "assistant":
        return
    out["turns"] += 1
    if isinstance(content, str):
        if content.strip():
            out["last_assistant_text"] = redact(content, rules)[:600]
        return
    if not isinstance(content, list):
        return
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "tool_use":
            inp = c.get("input") or {}
            brief = inp.get("command") or inp.get("file_path") or inp.get("query") or inp.get("pattern") or ""
            _add_tool(out, str(c.get("name")), brief, rules, max_tools)
            if c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit") and inp.get("file_path"):
                written.add(str(inp["file_path"]))
        elif c.get("type") == "text" and str(c.get("text", "")).strip():
            out["last_assistant_text"] = redact(c["text"], rules)[:600]


def _codex_event(
    ev: dict[str, Any], out: dict[str, Any], written: set[str], rules: list[tuple[str, str]], max_tools: int
) -> None:
    t, p = ev.get("type"), ev.get("payload") or {}
    if not isinstance(p, dict):
        return
    if t == "response_item":
        pt = p.get("type")
        if pt == "message" and p.get("role") == "assistant":
            out["turns"] += 1
            text = _text_of(p.get("content"))
            if text.strip():
                out["last_assistant_text"] = redact(text, rules)[:600]
        elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
            name = str(p.get("name") or ("local_shell" if pt == "local_shell_call" else ""))
            arguments = p.get("arguments") if pt == "function_call" else (p.get("input") or p.get("action") or "")
            tool, brief, files = _codex_tool(name, arguments)
            _add_tool(out, tool, brief, rules, max_tools)
            written.update(files)
    elif t == "event_msg":
        pt = p.get("type")
        if pt == "agent_message" and p.get("message"):
            out["turns"] += 1
            out["last_assistant_text"] = redact(str(p["message"]), rules)[:600]


def _gemini_event(
    ev: dict[str, Any], out: dict[str, Any], written: set[str], rules: list[tuple[str, str]], max_tools: int
) -> None:
    if ev.get("type") != "gemini":
        return
    out["turns"] += 1
    text = _text_of(ev.get("content"))
    if text.strip():
        out["last_assistant_text"] = redact(text, rules)[:600]
    for tc in ev.get("toolCalls") or []:
        if not isinstance(tc, dict):
            continue
        tool, brief, files = _gemini_tool(str(tc.get("name") or ""), tc.get("args"))
        _add_tool(out, tool, brief, rules, max_tools)
        written.update(files)
