"""Structural, redacted summary of an agent transcript (Claude Code JSONL). Tool output is never read."""

from __future__ import annotations

import json
import os
from typing import Any

from .redact import redact


def summarize(path: str | None, max_tools: int = 500) -> dict[str, Any]:
    out: dict[str, Any] = {"turns": 0, "tool_calls": [], "last_assistant_text": "", "files_written": []}
    if not path or not os.path.exists(path):
        return out
    written: set[str] = set()
    with open(path, errors="ignore") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("message") or {}
            role = msg.get("role") or ev.get("type")
            content = msg.get("content")
            if role != "assistant":
                continue
            out["turns"] += 1
            if isinstance(content, str):
                if content.strip():
                    out["last_assistant_text"] = redact(content)[:600]
                continue
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and len(out["tool_calls"]) < max_tools:
                    inp = c.get("input") or {}
                    brief = inp.get("command") or inp.get("file_path") or inp.get("query") or inp.get("pattern") or ""
                    out["tool_calls"].append({"tool": c.get("name"), "arg": redact(str(brief))[:160]})
                    if c.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit") and inp.get("file_path"):
                        written.add(str(inp["file_path"]))
                elif c.get("type") == "text" and str(c.get("text", "")).strip():
                    out["last_assistant_text"] = redact(c["text"])[:600]
    out["files_written"] = sorted(written)
    return out
