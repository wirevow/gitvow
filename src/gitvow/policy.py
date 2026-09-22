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
INDIRECT_TIERS = ("allow", "observe", "commit", "immediate")

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


# Words that precede the program without being it, and shells whose `-c` argument is a command line of its own.
_PREFIXES = frozenset({"sudo", "env", "time", "nohup", "exec", "command", "nice", "doas", "xargs"})
_PREFIX_WITH_ARG = frozenset({"timeout"})  # `timeout 30 kubectl …`
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n.*?\n\1[ \t]*(?=\n|$)", re.S)
_OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")", "\n"})


def _segments(text: str) -> list[list[str]] | None:
    """Each simple command in a shell line as its argv, quotes resolved, heredoc bodies dropped.

    None when the line cannot be tokenised (an unbalanced quote): the caller then falls back to matching the
    whole text, which fails toward asking.
    """
    body = _HEREDOC.sub("", text)
    lex = shlex.shlex(body, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        return None
    out: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        if tok in _OPERATORS or set(tok) <= set(";&|()"):
            if cur:
                out.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def _program_and_args(argv: list[str]) -> tuple[str, list[str]]:
    """Strip `VAR=x` assignments and prefixes such as sudo, env, time, timeout N; return (program, arguments)."""
    i = 0
    while i < len(argv):
        w = argv[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in _PREFIXES:
            i += 1
        elif w in _PREFIX_WITH_ARG:
            i += 2
        elif w.startswith("-") and i > 0 and argv[i - 1] in _PREFIXES | _PREFIX_WITH_ARG:
            i += 1  # an option to the prefix itself, e.g. `sudo -u root`, `env -i`
        else:
            break
    if i >= len(argv):
        return "", []
    return os.path.basename(argv[i]), argv[i + 1 :]


def match_program_rule(rule: dict[str, Any], text: str) -> str | None:
    """The program a `{program, verbs}` rule matches in **command position**, or None.

    Until 0.26.1 these rules were a regex over the whole command line, so `gh issue create --body "see kubectl
    exec"` was a cluster mutation and a commit message that mentioned `terraform apply` was an infrastructure
    change. Now the line is split into simple commands and the program must be the word that runs: after any
    `VAR=x`, `sudo`, `env`, `time`, `timeout N`, `nohup` or `xargs`, and inside a `sh -c "…"` argument, which is
    a command line of its own. Quoted arguments of some other program, and heredoc bodies, are not commands and
    are not matched. A line that cannot be tokenised falls back to the whole-text regex, failing toward asking.
    """
    for prog, _args in program_matches(rule, text):
        return prog
    return None


def program_matches(rule: dict[str, Any], text: str) -> list[tuple[str, list[str]]]:
    """Every simple command on the line that a `{program, verbs}` rule matches, as (program, arguments)."""
    progs = rule["program"] if isinstance(rule["program"], list) else [rule["program"]]
    verbs = "|".join(rule["verbs"])
    head = re.compile(rf"^{OPTS}\s+(?:{verbs})\b")
    segs = _segments(text)
    if segs is None:
        m = re.search(rule_pattern(rule), text)
        if not m:
            return []
        tail = text[m.start() :].lstrip()
        words = tail.split()
        return [(os.path.basename(words[0]), words[1:])] if words else []
    out: list[tuple[str, list[str]]] = []
    stack = list(segs)
    while stack:
        prog, args = _program_and_args(stack.pop())
        if not prog:
            continue
        if prog in _SHELLS and "-c" in args:
            inner = _segments(args[args.index("-c") + 1]) if args.index("-c") + 1 < len(args) else None
            if inner is None:
                if re.search(rule_pattern(rule), " ".join(args)):
                    out.append((progs[0], []))
            else:
                stack.extend(inner)
            continue
        if prog in progs and head.match(" " + " ".join(args)):
            out.append((prog, args))
    return out


def match_rule(
    rule: dict[str, Any], text: str, cwd: str | None = None
) -> tuple[str | None, re.Match[str] | None, str | None]:
    """(program, match, target) for whichever form the rule takes.

    `pattern` rules are a plain regex over the whole line. `{program, verbs}` rules match in command position, and
    when the rule also names a `target` regex, the command's target (targets.py: the kube context, the push
    remote and branch, the terraform workspace) is read and must match; a target that cannot be read matches, so
    an action of unknown reach is asked about rather than waved through.
    """
    if "pattern" in rule:
        m = re.search(rule["pattern"], text)
        return (None, m, None) if m else (None, None, None)
    from . import targets

    for prog, args in program_matches(rule, text):
        target = targets.resolve(prog, args, cwd) if rule.get("target") else None
        if targets.target_matches(rule, target):
            return prog, None, target
    return None, None, None


# Command shapes whose program the gate cannot read: the text names an indirection, not a program.
_INDIRECT_VAR = re.compile(r"^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$")


def indirect_commands(text: str) -> list[str]:
    """Descriptions of every simple command on the line whose program cannot be read from the text: `eval`,
    `source` / `.`, a variable in program position, or a shell running a script file rather than `-c`."""
    segs = _segments(text)
    if not segs:
        return []
    out: list[str] = []
    for seg in segs:
        prog, args = _program_and_args(seg)
        if not prog:
            continue
        if prog == "eval":
            out.append("eval")
        elif prog in ("source", "."):
            out.append(f"source {args[0]}" if args else "source")
        elif _INDIRECT_VAR.match(prog):
            out.append(f"{prog} (a variable in program position)")
        elif prog in _SHELLS and "-c" not in args:
            script = next((a for a in args if not a.startswith("-")), None)
            if script:
                out.append(f"{prog} {script}")
    return out


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


def load_policy(cwd: str | None = None, home: str | None = None, pack: bool = True) -> dict[str, Any]:
    """Precedence: <repo>/.gitvow/policy.json → ~/.gitvow/policy.json → package default. Then the organisation
    pack cached by `gitvow sync`, if any, is applied as a layer: rules added, settings tightened, never loosened
    (see pack.py). A pack that is missing, expired or malformed changes nothing."""
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
            return _with_pack(pol, cwd, home) if pack else pol
    raise PolicyError("no policy.json found")


def _with_pack(pol: dict[str, Any], cwd: str, home: str) -> dict[str, Any]:
    from . import pack as pk

    try:
        found = pk.load_pack(cwd, home)
    except Exception:  # the cache is advisory; nothing in it may stop the gate from loading its own policy
        return pol
    if found is None:
        return pol
    try:
        return pk.apply(pol, found)
    except PolicyError as e:
        out = dict(pol)
        out["_pack"] = {"applied": False, "error": str(e)[:300], "path": found.get("path"), "names": found.get("names")}
        return out


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
            if "target" in rule:
                if key == "path_confirm" or "pattern" in rule:
                    raise PolicyError(f"{path}: {key} 'target' is only for rules written as program and verbs")
                try:
                    re.compile(rule["target"])
                except (re.error, TypeError) as e:
                    raise PolicyError(f"{path}: bad regex in {key} target: {rule['target']!r} ({e})") from e
    dec = pol.get("decisions", {})
    if not isinstance(dec, dict):
        raise PolicyError(f"{path}: decisions must be an object")
    if dec.get("mode", "open") not in DECISION_MODES:
        raise PolicyError(f"{path}: decisions.mode must be one of {DECISION_MODES}")
    for key in ("authorities", "production_branches"):
        if not isinstance(dec.get(key, []), list):
            raise PolicyError(f"{path}: decisions.{key} must be a list")
    for key in ("rule_threshold", "rule_decay_days", "commit_window_seconds"):
        v = dec.get(key, 1)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise PolicyError(f"{path}: decisions.{key} must be a positive integer")
    if not isinstance(dec.get("session_scope", True), bool):
        raise PolicyError(f"{path}: decisions.session_scope must be true or false")
    if dec.get("indirect_commands", "observe") not in INDIRECT_TIERS:
        raise PolicyError(f"{path}: decisions.indirect_commands must be one of {INDIRECT_TIERS}")
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
            prog, m, _t = match_rule(r, text, cwd)
            if prog or m:
                return Decision("deny", r.get("reason", "denied"), text)
        for r in pol.get("bash_confirm", []):
            prog, m, target = match_rule(r, text, cwd)
            if prog or m:
                when = r.get("when", "immediate")
                reason = r.get("reason", "needs confirmation")
                if prog:
                    # The target is part of the subject, so standing accrues per place reached: three answers
                    # for context=staging say nothing about context=prod.
                    subject = f"{prog} ({reason})" + (f" @ {target}" if target else "")
                    subject = subject[:160]
                else:
                    subject = _command_subject(text, m, reason)  # type: ignore[arg-type]
                evidence = [text.strip()[:200]] + ([f"target: {target}"] if target else [])
                f = _finding("command", subject, "", reason, evidence)
                if when == "immediate":
                    # The finding travels with the confirm so the hook can record it and a person can answer
                    # it once for the session; the outcome is still an immediate stop.
                    return Decision("confirm", reason, text, "immediate", (f,))
                f["observe"] = when == "observe"
                findings.append(f)
        tier = (pol.get("decisions") or {}).get("indirect_commands", "observe")
        if tier != "allow":
            for desc in indirect_commands(text):
                reason = "runs a command the gate cannot read"
                f = _finding("command", f"{desc} ({reason})"[:160], "", reason, [text.strip()[:200]])
                if tier == "immediate":
                    return Decision("confirm", reason, text, "immediate", (f,))
                f["observe"] = tier == "observe"
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
