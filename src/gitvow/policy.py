"""Tool-call policy: deny → confirm → allow, evaluated on Bash command text, edited file paths, and MCP tool names."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any

from . import providers as _providers

DEFAULT_POLICY_PATH = os.path.join(os.path.dirname(__file__), "default_policy.json")


class PolicyError(Exception):
    """Raised when no valid policy can be loaded. Callers must fail closed."""


WHEN = ("immediate", "commit")
DECISION_MODES = ("open", "strict")


@dataclass(frozen=True)
class Decision:
    outcome: str  # "allow" | "deny" | "confirm"
    reason: str = ""
    detail: str = ""
    when: str = "immediate"  # for confirm: ask now, or collect and ask on the card at commit
    findings: tuple[dict[str, Any], ...] = ()  # at-commit findings this call raised

    @property
    def blocks(self) -> bool:
        return self.outcome == "deny" or (self.outcome == "confirm" and self.when == "immediate")

    @property
    def deferred(self) -> bool:
        return self.outcome == "confirm" and self.when == "commit"


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
            if "when" in rule and rule["when"] not in WHEN:
                raise PolicyError(f"{path}: {key} 'when' must be one of {WHEN}")
    dec = pol.get("decisions", {})
    if not isinstance(dec, dict):
        raise PolicyError(f"{path}: decisions must be an object")
    if dec.get("mode", "open") not in DECISION_MODES:
        raise PolicyError(f"{path}: decisions.mode must be one of {DECISION_MODES}")
    for key in ("authorities", "production_branches"):
        if not isinstance(dec.get(key, []), list):
            raise PolicyError(f"{path}: decisions.{key} must be a list")
    for key in ("mcp_allow", "mcp_deny"):
        for pat in pol.get(key, []):
            try:
                re.compile(pat)
            except re.error as e:
                raise PolicyError(f"{path}: bad regex in {key}: {pat} ({e})") from e
    snap = pol.get("snapshots", {})
    if not isinstance(snap, dict) or not isinstance(snap.get("exclude", []), list):
        raise PolicyError(f"{path}: snapshots must be an object with an optional 'exclude' list")
    provs = pol.get("providers", [])
    if not isinstance(provs, list):
        raise PolicyError(f"{path}: providers must be a list")
    for prov in provs:
        if not isinstance(prov, dict) or not prov.get("name") or not prov.get("command"):
            raise PolicyError(f"{path}: each provider needs 'name' and 'command'")
        bad = [q for q in prov.get("questions", []) if q not in _providers.QUESTIONS]
        if bad:
            raise PolicyError(f"{path}: provider {prov['name']} lists unknown questions {bad}")


def _finding(kind: str, subject: str, path: str, reason: str, evidence: list[str] | None = None) -> dict[str, Any]:
    if kind == "command":
        text = f"run {subject}"
    elif kind == "route":
        text = f"route {subject} in {path}" if path else f"route {subject}"
    elif kind == "route-removal":
        text = f"remove route {subject} from {path}" if path else f"remove route {subject}"
    else:
        text = f"edit {subject}"
    return {
        "finding": text,
        "kind": kind,
        "subject": subject,
        "path": path,
        "reason": reason,
        "evidence": evidence or [],
    }


def evaluate(pol: dict[str, Any], tool: str, tool_input: dict[str, Any], cwd: str | None = None) -> Decision:
    text = tool_input.get("command", "") if tool == "Bash" else ""
    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
    findings: list[dict[str, Any]] = []
    if tool == "Bash":
        for r in pol.get("bash_deny", []):
            if re.search(r["pattern"], text):
                return Decision("deny", r.get("reason", "denied"), text)
        for r in pol.get("bash_confirm", []):
            if re.search(r["pattern"], text):
                when = r.get("when", "immediate")
                if when == "immediate":
                    return Decision("confirm", r.get("reason", "needs confirmation"), text)
                findings.append(_finding("command", text.strip()[:120], "", r.get("reason", "needs confirmation")))
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path:
        for r in pol.get("path_confirm", []):
            if re.search(r["pattern"], path):
                if r.get("when", "commit") == "immediate":
                    return Decision("confirm", r.get("reason", "sensitive path"), path)
                findings.append(_finding("edit", _rel(path, cwd), _rel(path, cwd), r.get("reason", "sensitive path")))
                break
    if tool.startswith("mcp__"):
        if any(re.fullmatch(p, tool) for p in pol.get("mcp_deny", [])):
            return Decision("deny", "MCP tool on deny list", tool)
        allow = pol.get("mcp_allow", [])
        if allow and not any(re.fullmatch(p, tool) for p in allow):
            return Decision("confirm", "MCP tool not on allow list", tool)
    if pol.get("providers") and tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        hits = [a for a in _providers.ask(pol, tool, tool_input, cwd) if a.blocks]
        failed = [h for h in hits if h.answer == "failed"]
        if failed:
            return Decision("confirm", "; ".join(h.text() for h in failed[:3]), path)
        kinds = {"gate_bearing": "gate-file", "route_gate": "route", "route_callers": "route-removal"}
        for h in hits:
            findings.append(_finding(kinds[h.question], h.subject, _rel(path, cwd), h.text(), h.evidence))
    if findings:
        return Decision(
            "confirm", "; ".join(f["reason"] for f in findings[:3]), path or text, "commit", tuple(findings)
        )
    clf = pol.get("llm_classifier") or {}
    if clf.get("enabled") and clf.get("command"):
        return _classify(clf["command"], tool, tool_input, text or path or tool)
    return Decision("allow")


def _rel(path: str, cwd: str | None) -> str:
    if path and os.path.isabs(path) and cwd:
        rel = os.path.relpath(path, cwd)
        return rel if not rel.startswith("..") else path
    return path


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
    if d.deferred:
        return f"RECORDED for decision at commit ({d.reason})."
    return (
        f"CONFIRMATION REQUIRED ({d.reason}). Ask the user explicitly before doing this; "
        "if they confirm, tell them to re-run with the policy exception or perform it manually."
    )
