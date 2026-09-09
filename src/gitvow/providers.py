"""Providers: external programs the gate asks factual questions (gate_bearing, route_gate, route_callers)."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any

PROTOCOL = 1
QUESTIONS = ("gate_bearing", "route_gate", "route_callers")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
ROUTE_RE = re.compile(r"""["'`](/[A-Za-z0-9_\-./{}:*$]*)["'`]""")


@dataclass
class Answer:
    provider: str
    question: str
    subject: str
    answer: str  # yes | no | unknown | failed
    evidence: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> bool:
        return self.answer in ("yes", "failed")

    def text(self) -> str:
        why = "; ".join(self.evidence[:4]) if self.evidence else ""
        if self.answer == "failed":
            return f"provider {self.provider} unavailable for {self.question} {self.subject}"
        return f"provider {self.provider}: {self.question} {self.subject}" + (f": {why}" if why else "")


def route_literals(text: str) -> list[str]:
    """Quoted strings that look like routes: begin with '/', no spaces, at least one more character."""
    out: list[str] = []
    for m in ROUTE_RE.finditer(text or ""):
        r = m.group(1)
        if len(r) > 1 and not r.startswith("//") and "." not in os.path.basename(r) and r not in out:
            out.append(r)
    return out


def questions_for(tool: str, tool_input: dict[str, Any]) -> list[tuple[str, str]]:
    """(question, subject) pairs the gate should ask for this tool call."""
    if tool not in EDIT_TOOLS:
        return []
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    qs: list[tuple[str, str]] = []
    if path:
        qs.append(("gate_bearing", path))
    old, new = "", ""
    if tool == "Edit":
        old, new = str(tool_input.get("old_string") or ""), str(tool_input.get("new_string") or "")
    elif tool == "MultiEdit":
        for e in tool_input.get("edits") or []:
            old += "\n" + str(e.get("old_string") or "")
            new += "\n" + str(e.get("new_string") or "")
    elif tool == "Write":
        new = str(tool_input.get("content") or "")
    before, after = set(route_literals(old)), set(route_literals(new))
    qs += [("route_gate", r) for r in sorted(after - before)]
    qs += [("route_callers", r) for r in sorted(before - after)]
    return qs


def _run(prov: dict[str, Any], question: str, subject: str, repo: str, path: str, tool: str) -> Answer:
    name = str(prov.get("name") or "provider")
    req = {
        "protocol": PROTOCOL,
        "question": question,
        "subject": subject,
        "repo": repo,
        "path": path,
        "tool_name": tool,
    }
    try:
        argv = shlex.split(str(prov.get("command") or ""))
        if not argv:
            return Answer(name, question, subject, "failed", ["empty command"])
        r = subprocess.run(
            argv,
            input=json.dumps(req),
            capture_output=True,
            text=True,
            timeout=float(prov.get("timeout") or 10),
            check=False,
            cwd=repo or None,
        )
        if r.returncode != 0:
            return Answer(name, question, subject, "failed", [f"exit {r.returncode}"])
        data = json.loads(r.stdout)
        ans = str(data.get("answer", "unknown")).lower()
        if ans not in ("yes", "no", "unknown"):
            return Answer(name, question, subject, "failed", ["bad answer"])
        ev = [str(e)[:200] for e in (data.get("evidence") or [])[:8]]
        return Answer(name, question, subject, ans, ev)
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError) as e:
        return Answer(name, question, subject, "failed", [type(e).__name__])


def ask(
    pol: dict[str, Any],
    tool: str,
    tool_input: dict[str, Any],
    cwd: str | None = None,
    only: list[tuple[str, str]] | None = None,
) -> list[Answer]:
    provs = pol.get("providers") or []
    if not provs:
        return []
    repo = cwd or os.getcwd()
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    if path and os.path.isabs(path):
        rel = os.path.relpath(path, repo)
        path = rel if not rel.startswith("..") else path
    answers: list[Answer] = []
    for q, subj in only if only is not None else questions_for(tool, {**tool_input, "file_path": path}):
        for prov in provs:
            if q in (prov.get("questions") or QUESTIONS):
                answers.append(_run(prov, q, subj, repo, path, tool))
    return answers
