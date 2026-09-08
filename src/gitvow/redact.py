"""Redaction of secrets and personal data at write time. Best effort by design; see SECURITY.md."""

from __future__ import annotations

import hashlib
import json
import math
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
