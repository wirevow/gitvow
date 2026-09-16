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


WHEN = ("immediate", "commit", "observe")
DECISION_MODES = ("open", "strict")

# Options that may sit between a program and its verb: `--context prod`, `-n ns`, `--kubeconfig=x`, `-v`. Three
# shapes: `--k=v`, `--k v` where v does not start with a dash or a shell metacharacter, and a bare flag. Rules written
# as {"program": ..., "verbs": [...]} are compiled with this between the two, so `kubectl --context prod delete`
# is a kubectl delete. Until 0.17.1 the default rules anchored the verb to the word right after the program, and a
# replay of three engineers' real sessions found 163 cluster mutations, 49 of them deletes against
# production-named contexts, that had walked past the gate on a `--context` flag. Same bug class as the `git -c`
# form the commit gate mis-parsed until 0.16.
OPTS = r"(?:\s+(?:-{1,2}[\w.-]+=\S*|-{1,2}[\w.-]+\s+[^-\s|;&<>()]\S*|-{1,2}[\w.-]+))*"


def rule_pattern(rule: dict[str, Any]) -> str:
    """The regex a command rule matches with: its `pattern`, or one built from `program` and `verbs`."""
    if "pattern" in rule:
        return rule["pattern"]
    progs = rule["program"] if isinstance(rule["program"], list) else [rule["program"]]
    verbs = "|".join(rule["verbs"])
    return rf"\b(?:{'|'.join(re.escape(p) for p in progs)}){OPTS}\s+(?:{verbs})\b"


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

    @property
    def observed(self) -> bool:
        """Findings recorded on the commit that nobody is asked about. The call runs; the record grows; no card."""
        return self.outcome == "confirm" and self.when == "observe"


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
            if not isinstance(rule, dict):
                raise PolicyError(f"{path}: {key} entries must be objects")
            has_verbs = key != "path_confirm" and "program" in rule and "verbs" in rule
            if "pattern" not in rule and not has_verbs:
                need = "a 'pattern'" if key == "path_confirm" else "a 'pattern', or 'program' and 'verbs'"
                raise PolicyError(f"{path}: {key} entries need {need}")
            if has_verbs and "pattern" not in rule:
                progs = rule["program"] if isinstance(rule["program"], list) else [rule["program"]]
                if not progs or not all(isinstance(p, str) and p for p in progs):
                    raise PolicyError(f"{path}: {key} 'program' must be a non-empty string or list of them")
                if (
                    not isinstance(rule["verbs"], list)
                    or not rule["verbs"]
                    or not all(isinstance(v, str) and v for v in rule["verbs"])
                ):
                    raise PolicyError(f"{path}: {key} 'verbs' must be a non-empty list of strings")
            pat = rule_pattern(rule) if key != "path_confirm" else rule["pattern"]
            try:
                re.compile(pat)
            except re.error as e:
                raise PolicyError(f"{path}: bad regex in {key}: {pat} ({e})") from e
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
    for key in ("rule_threshold", "rule_decay_days"):
        v = dec.get(key, 1)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise PolicyError(f"{path}: decisions.{key} must be a positive integer")
    if not isinstance(dec.get("session_scope", True), bool):
        raise PolicyError(f"{path}: decisions.session_scope must be true or false")
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


def _command_subject(text: str, match: re.Match[str], reason: str) -> str:
    """What a command finding is *about*: the program the rule matched, named by the rule that stopped it.

    Until 0.16 this was the whole command line, so `kubectl apply -f a.yaml` and `kubectl apply -f b.yaml`
    were two different findings for ever. A person answered the same question again on every invocation, and
    no command could reach `decisions.rule_threshold` even in a repository that ran it daily: the arguments
    are precisely the part that changes per run, so a subject built from them converges on nothing. Every
    other finding kind already named the thing being decided about — a path, a route — and this one named the
    typing. The program plus the rule that objected is that thing, and it is the rule rather than the program
    alone because two rules can match the same program for unrelated reasons, and collapsing those into one
    finding would let a precedent set for one consequence answer the other. The full command line travels as
    evidence instead, so the card still shows exactly what was going to run.
    """
    tail = text[match.start() :].lstrip()
    # Split at the first shell metacharacter rather than taking the whole match: a pattern is free to span
    # arguments (`git push .*--force`), and anything after the program name is per-invocation again.
    head = re.split(r"[\s;|&<>()]", tail, maxsplit=1)[0].strip("\"'") if tail else ""
    exe = os.path.basename(head) or head or (text.strip().split(" ", 1)[0] if text.strip() else "command")
    return f"{exe} ({reason})"[:120]


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
            if re.search(rule_pattern(r), text):
                return Decision("deny", r.get("reason", "denied"), text)
        for r in pol.get("bash_confirm", []):
            m = re.search(rule_pattern(r), text)
            if m:
                when = r.get("when", "immediate")
                reason = r.get("reason", "needs confirmation")
                f = _finding("command", _command_subject(text, m, reason), "", reason, [text.strip()[:200]])
                if when == "immediate":
                    # The finding travels with the confirm so the hook can record it and a person can answer
                    # it once for the session; the outcome is still an immediate stop.
                    return Decision("confirm", reason, text, "immediate", (f,))
                f["observe"] = when == "observe"
                findings.append(f)
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit") and path:
        for r in pol.get("path_confirm", []):
            if re.search(r["pattern"], path):
                f = _finding("edit", _rel(path, cwd), _rel(path, cwd), r.get("reason", "sensitive path"))
                if r.get("when", "commit") == "immediate":
                    return Decision("confirm", r.get("reason", "sensitive path"), path, "immediate", (f,))
                f["observe"] = r.get("when", "commit") == "observe"
                findings.append(f)
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
        # A call that raised only observe findings is not deferred to the card; it is recorded and runs.
        when = "observe" if all(f.get("observe") for f in findings) else "commit"
        return Decision("confirm", "; ".join(f["reason"] for f in findings[:3]), path or text, when, tuple(findings))
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
    if d.observed:
        return f"RECORDED ({d.reason}); nobody is asked."
    return (
        f"CONFIRMATION REQUIRED ({d.reason}). Ask the user explicitly before doing this; "
        "if they confirm, tell them to re-run with the policy exception or perform it manually."
    )


def confirm_message(d: Decision, n: int | None, session_scope: bool) -> str:
    """The immediate-confirm message, with the finding's number so a person's answer can be recorded once."""
    if n is None or not session_scope:
        return message_for(d)
    return (
        f"CONFIRMATION REQUIRED ({d.reason}). Ask the user before doing this. If they agree, record it and run the "
        f'command again:\n  gitvow decide {n} accept --scope session [--reason "<phrase>"]\n'
        f"The answer holds for this session, so this question is not asked again until the session ends, and it goes "
        f"into the next commit. If they refuse: gitvow decide {n} decline."
    )
