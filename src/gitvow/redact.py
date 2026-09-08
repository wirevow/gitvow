"""Redaction of secrets and personal data at write time. Best effort by design; see SECURITY.md."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _email_token(m: re.Match[str]) -> str:
    return "[email:" + hashlib.sha256(m.group(0).lower().encode()).hexdigest()[:8] + "]"


PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[aws-access-key]"),
    (re.compile(r"(?i)(aws_secret_access_key|secret_access_key)\s*[:=]\s*\S+"), r"\1=[redacted]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[jwt]"),
    (re.compile(r"\b(gh[pousr]|github_pat)_[A-Za-z0-9_]{20,}"), "[github-token]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "[api-key]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[slack-token]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}"), "[google-api-key]"),
    (
        re.compile(
            r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization|bearer)\s*[:=]\s*[\"']?[^\s\"']{6,}"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)\b(mysql|postgres(?:ql)?|redis|mongodb(?:\+srv)?|amqp)://[^\s\"']+"), r"\1://[redacted-dsn]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[private-key]"),
    (_EMAIL, _email_token),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[ssn-like]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b(?![ -]?\d)"), "[card-like-number]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
]

_ENTROPY_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{20,}")


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def redact_high_entropy(s: str, threshold: float = 4.5, min_len: int = 20) -> str:
    """Replace long alphanumeric runs whose Shannon entropy exceeds the threshold. Catches unknown secret formats."""

    def repl(m: re.Match[str]) -> str:
        tok = m.group(0)
        if len(tok) >= min_len and shannon_entropy(tok) > threshold and not tok.startswith(("http", "/")):
            return "[high-entropy]"
        return tok

    return _ENTROPY_TOKEN.sub(repl, s)


def redact(value: object, custom: Iterable[tuple[str, str]] = ()) -> str:
    """Redact a string (or JSON-serialisable value). Custom rules are (regex, replacement) pairs applied first."""
    s = value if isinstance(value, str) else json.dumps(value, default=str)
    for pat, custom_rep in custom:
        s = re.sub(pat, custom_rep, s)
    for rx, rep in PATTERNS:
        s = rx.sub(rep, s)
    return redact_high_entropy(s)


class RedactionError(Exception):
    """A rules file is unreadable or a pattern does not compile. Callers must write nothing."""


RULES_FILENAME = "redact-rules.json"


def _read_rules(path: str) -> list[tuple[str, str]]:
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError as e:
        raise RedactionError(f"{path}: {e.strerror or e}") from e
    except json.JSONDecodeError as e:
        raise RedactionError(f"{path}: invalid JSON ({e.msg} at line {e.lineno})") from e
    if isinstance(data, dict):
        data = data.get("rules", [])
    if not isinstance(data, list):
        raise RedactionError(f"{path}: expected a list of rules")
    out: list[tuple[str, str]] = []
    for i, r in enumerate(data):
        if (
            not isinstance(r, dict)
            or not isinstance(r.get("pattern"), str)
            or not isinstance(r.get("replacement"), str)
        ):
            raise RedactionError(f"{path}: rule {i} needs string 'pattern' and 'replacement'")
        try:
            re.compile(r["pattern"])
        except re.error as e:
            raise RedactionError(f"{path}: rule {i} pattern does not compile ({e})") from e
        out.append((r["pattern"], r["replacement"]))
    return out


def load_rules(cwd: str | None = None, home: str | None = None) -> list[tuple[str, str]]:
    """Custom rules from <repo>/.gitvow/redact-rules.json then ~/.gitvow/redact-rules.json. Both apply.

    Raises RedactionError on any unreadable or invalid file: callers must then write nothing.
    """
    rules: list[tuple[str, str]] = []
    for base in (cwd, home or os.path.expanduser("~")):
        if not base:
            continue
        path = os.path.join(base, ".gitvow", RULES_FILENAME)
        if os.path.exists(path):
            rules += _read_rules(path)
    return rules
