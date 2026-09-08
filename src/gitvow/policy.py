"""Tool-call policy: deny → confirm → allow, evaluated on Bash command text, edited file paths, and MCP tool names."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(__file__), "default_policy.json")


class PolicyError(Exception):
    """Raised when no valid policy can be loaded. Callers must fail closed."""


@dataclass(frozen=True)
class Decision:
    outcome: str  # "allow" | "deny" | "confirm"
    reason: str = ""
    detail: str = ""

    @property
    def blocks(self) -> bool:
        return self.outcome != "allow"


def load_policy(cwd: str | None = None, home: str | None = None) -> dict[str, Any]:
    """Precedence: <repo>/.gitvow/policy.json → ~/.gitvow/policy.json → package default."""
    cwd = cwd or os.getcwd()
    home = home or os.path.expanduser("~")
    for p in (
        os.path.join(cwd, ".gitvow", "policy.json"),
        os.path.join(home, ".gitvow", "policy.json"),
        DEFAULT_POLICY_PATH,
    ):
        if os.path.exists(p):
            try:
                with open(p) as fh:
                    pol = json.load(fh)
            except (OSError, json.JSONDecodeError) as e:
                raise PolicyError(f"{p}: {e}") from e
            _validate(pol, p)
            return pol
    raise PolicyError("no policy.json found")


def _validate(pol: dict[str, Any], path: str) -> None:
    for key in ("bash_deny", "bash_confirm", "path_confirm"):
        for rule in pol.get(key, []):
            if not isinstance(rule, dict) or "pattern" not in rule:
                raise PolicyError(f"{path}: {key} entries need a 'pattern'")
            try:
                re.compile(rule["pattern"])
            except re.error as e:
                raise PolicyError(f"{path}: bad regex in {key}: {rule['pattern']} ({e})") from e
    for key in ("mcp_allow", "mcp_deny"):
        for pat in pol.get(key, []):
            try:
                re.compile(pat)
            except re.error as e:
                raise PolicyError(f"{path}: bad regex in {key}: {pat} ({e})") from e


def evaluate(pol: dict[str, Any], tool: str, tool_input: dict[str, Any]) -> Decision:
    text = tool_input.get("command", "") if tool == "Bash" else ""
    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
    if tool == "Bash":
        for r in pol.get("bash_deny", []):
            if re.search(r["pattern"], text):
                return Decision("deny", r.get("reason", "denied"), text)
        for r in pol.get("bash_confirm", []):
            if re.search(r["pattern"], text):
                return Decision("confirm", r.get("reason", "needs confirmation"), text)
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path:
        for r in pol.get("path_confirm", []):
            if re.search(r["pattern"], path):
                return Decision("confirm", r.get("reason", "sensitive path"), path)
    if tool.startswith("mcp__"):
        if any(re.fullmatch(p, tool) for p in pol.get("mcp_deny", [])):
            return Decision("deny", "MCP tool on deny list", tool)
        allow = pol.get("mcp_allow", [])
        if allow and not any(re.fullmatch(p, tool) for p in allow):
            return Decision("confirm", "MCP tool not on allow list", tool)
    clf = pol.get("llm_classifier") or {}
    if clf.get("enabled") and clf.get("command"):
        return _classify(clf["command"], tool, tool_input, text or path or tool)
    return Decision("allow")


def _classify(command: str, tool: str, tool_input: dict[str, Any], detail: str) -> Decision:
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    try:
        argv = shlex.split(command) if isinstance(command, str) else [str(a) for a in command]
        if not argv:
            return Decision("confirm", "classifier command is empty", detail)
        # No shell: the policy file names a program and its arguments, so policy text
        # can never become shell syntax.
        r = subprocess.run(argv, input=payload, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return Decision("confirm", "classifier unavailable", detail)
    parts = r.stdout.strip().split(maxsplit=1) or ["ALLOW"]
    verdict, reason = parts[0].upper(), (parts[1] if len(parts) > 1 else "")
    if verdict == "DENY":
        return Decision("deny", "classifier: " + reason, detail)
    if verdict == "CONFIRM":
        return Decision("confirm", "classifier: " + reason, detail)
    return Decision("allow")


def message_for(d: Decision) -> str:
    if d.outcome == "deny":
        return f"BLOCKED by policy ({d.reason})."
    return (
        f"CONFIRMATION REQUIRED ({d.reason}). Ask the user explicitly before doing this; "
        "if they confirm, tell them to re-run with the policy exception or perform it manually."
    )
