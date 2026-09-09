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
        "usage": _empty_usage(),
        "subagents": {"count": 0, "tool_calls": 0},
    }
    if not path or not os.path.exists(path):
        _finish_usage(out["usage"])
        return out
    rules = list(rules)
    written: set[str] = set()
    fmt: str | None = None
    out["_seen_requests"] = set()
    out["_side_ids"] = set()
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
    out.pop("_codex_model", None)
    out.pop("_seen_requests", None)
    out.pop("_side_ids", None)
    _finish_usage(out["usage"])
    return out


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "models": {},
        "by_model": {},
    }


def _bump(
    usage: dict[str, Any],
    model: str,
    input_t: int = 0,
    output_t: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
) -> None:
    usage["input_tokens"] += input_t
    usage["output_tokens"] += output_t
    usage["cache_read_tokens"] += cache_read
    usage["cache_write_tokens"] += cache_write
    usage["reasoning_tokens"] += reasoning
    m = model or "unknown"
    usage["models"][m] = usage["models"].get(m, 0) + 1
    b = usage["by_model"].setdefault(m, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0})
    b["input"] += input_t
    b["output"] += output_t
    b["cache_read"] += cache_read
    b["cache_write"] += cache_write
    b["reasoning"] += reasoning


def _finish_usage(usage: dict[str, Any]) -> None:
    usage["total_tokens"] = (
        usage["input_tokens"]
        + usage["output_tokens"]
        + usage["cache_read_tokens"]
        + usage["cache_write_tokens"]
        + usage["reasoning_tokens"]
    )


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
    side = bool(ev.get("isSidechain"))
    if side:
        sid = ev.get("agentId") or ev.get("sessionId") or ev.get("parentUuid") or "side"
        if sid not in out["_side_ids"]:
            out["_side_ids"].add(sid)
            out["subagents"]["count"] += 1
        if isinstance(content, list):
            out["subagents"]["tool_calls"] += sum(
                1 for c in content if isinstance(c, dict) and c.get("type") == "tool_use"
            )
    u = msg.get("usage") if isinstance(msg, dict) else None
    if isinstance(u, dict):
        # streamed chunks of one response repeat the same usage: count each request once
        req = ev.get("requestId") or ev.get("uuid")
        if req not in out["_seen_requests"]:
            out["_seen_requests"].add(req)
            _bump(
                out["usage"],
                str(msg.get("model") or ""),
                int(u.get("input_tokens") or 0),
                int(u.get("output_tokens") or 0),
                int(u.get("cache_read_input_tokens") or 0),
                int(u.get("cache_creation_input_tokens") or 0),
            )
    if side:
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
    elif t == "turn_context":
        if p.get("model"):
            out["_codex_model"] = str(p["model"])
    elif t == "event_msg":
        pt = p.get("type")
        if pt == "agent_message" and p.get("message"):
            out["turns"] += 1
            out["last_assistant_text"] = redact(str(p["message"]), rules)[:600]
        elif pt == "token_count":
            info = p.get("info") or {}
            tot = info.get("total_token_usage") or info.get("total") or {}
            if isinstance(tot, dict) and tot:
                # running totals: replace rather than add
                model = out.get("_codex_model") or str(p.get("model") or "codex")
                out["usage"] = _empty_usage()
                inp = int(tot.get("input_tokens") or 0)
                cached = int(tot.get("cached_input_tokens") or 0)
                _bump(
                    out["usage"],
                    model,
                    max(inp - cached, 0),
                    int(tot.get("output_tokens") or 0),
                    cached,
                    0,
                    int(tot.get("reasoning_output_tokens") or 0),
                )


def _gemini_event(
    ev: dict[str, Any], out: dict[str, Any], written: set[str], rules: list[tuple[str, str]], max_tools: int
) -> None:
    if ev.get("type") != "gemini":
        return
    out["turns"] += 1
    tk = ev.get("tokens")
    if isinstance(tk, dict):
        _bump(
            out["usage"],
            str(ev.get("model") or "gemini"),
            int(tk.get("input") or 0),
            int(tk.get("output") or 0) + int(tk.get("tool") or 0),
            int(tk.get("cached") or 0),
            0,
            int(tk.get("thoughts") or 0),
        )
    text = _text_of(ev.get("content"))
    if text.strip():
        out["last_assistant_text"] = redact(text, rules)[:600]
    for tc in ev.get("toolCalls") or []:
        if not isinstance(tc, dict):
            continue
        tool, brief, files = _gemini_tool(str(tc.get("name") or ""), tc.get("args"))
        _add_tool(out, tool, brief, rules, max_tools)
        written.update(files)
