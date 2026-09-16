"""The export bundle: the record leaves the machine as a directory a security team can read first.

A bundle carries the record, never the session: decisions, confirmed claims, observed findings, rule verdicts,
and, when the repository has consented, session summaries and gate-event counts, plus the meter. No transcript
text, no working tree, no snapshot, no command line, no prompt. One file per consented data class, so a reviewer
learns what travels by listing the directory; a class not consented has no file and the manifest says so.

Redaction is re-verified, never applied: a row that trips a redaction rule at export is refused and counted in
the attestation, because a rewritten row would mean the write-time edge failed and the failure was hidden.

Every part is serialised canonically (rows sorted by key, keys sorted, no timestamps the exporter invented) and
hashed; the manifest's digest is the idempotency key a store uses, and the frontier says exactly what the bundle
covers. Contract: the private drafts under the strategy repository, `contracts/export-bundle.md`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import date, timedelta
from typing import Any

from . import __version__
from .claims import confirmed_and_rejected
from .decisions import SESSION_RE, parse_trailers
from .redact import redact
from .rules import RULES_NOTE_HEADER, RULES_NOTES_REF, rule_decisions
from .scan import COMPILED as AGENT_MARKS
from .state import git, git_dir, toplevel

SCHEMA = 1
KIND = "record"
CONSENT_FILE = os.path.join(".gitvow", "export.json")
CLASSES = ("decisions", "claims", "observed", "rule_verdicts", "sessions", "gate_events", "meter")
DEFAULT_CONSENT = {
    "decisions": True,
    "claims": True,
    "observed": True,
    "rule_verdicts": True,
    "sessions": False,
    "gate_events": False,
    "meter": True,
}
PART_FILES = {
    "decisions": "decisions.jsonl",
    "claims": "claims.jsonl",
    "observed": "observed.jsonl",
    "rule_verdicts": "rule-verdicts.jsonl",
    "sessions": "sessions.jsonl",
    "gate_events": "gate-events.jsonl",
    "meter": "meter.json",
}


def consent(cwd: str, override: list[str] | None = None) -> dict[str, bool]:
    """What this repository lets leave: the committed `.gitvow/export.json`, or the defaults; `override` names the
    classes to export instead, for a single run."""
    if override is not None:
        bad = [c for c in override if c not in CLASSES]
        if bad:
            raise ValueError(f"unknown export class(es) {bad}; known: {', '.join(CLASSES)}")
        return {c: c in override for c in CLASSES}
    top = toplevel(cwd) or cwd
    p = os.path.join(top, CONSENT_FILE)
    out = dict(DEFAULT_CONSENT)
    if os.path.exists(p):
        with open(p) as fh:
            data = json.load(fh)
        for k, v in (data.get("consent") or data).items():
            if k in CLASSES and isinstance(v, bool):
                out[k] = v
    return out


def remote_name(cwd: str) -> str:
    """host/owner/repo, no scheme, no `.git`; the join key across bundles from the same repository."""
    rc, url, _ = git(["remote", "get-url", "origin"], cwd)
    if rc != 0 or not url:
        return os.path.basename((toplevel(cwd) or cwd).rstrip("/"))
    url = url.strip()
    url = re.sub(r"^[a-z+]+://", "", url)
    url = re.sub(r"^[^@]+@", "", url)
    url = url.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", url) else url
    url = re.sub(r"\.git$", "", url).strip("/")
    return url


def since_date(since: str | None) -> str | None:
    if not since:
        return None
    m = re.fullmatch(r"(\d+)([dmy])", since)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n if unit == "d" else n * 30 if unit == "m" else n * 365
        return (date.today() - timedelta(days=days)).isoformat()
    return since[:10]


def _log(cwd: str, since: str | None) -> list[tuple[str, str, str]]:
    args = ["log", "--format=%H%x00%ad%x00%B%x01", "--date=short"]
    if since:
        args.append(f"--since={since}")
    rc, out, _ = git(args, cwd)
    if rc != 0:
        return []
    rows = []
    for rec in out.split("\x01"):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        sha, day, body = rec.split("\x00", 2)
        rows.append((sha, day, body))
    return rows


def _session_note(cwd: str, sha: str, body: str) -> dict[str, Any]:
    m = SESSION_RE.search(body)
    if not m:
        return {}
    sid = re.sub(r"[^A-Za-z0-9._-]", "_", m.group(1))
    rc, note, _ = git(["notes", f"--ref=gitvow/{sid}", "show", sha], cwd)
    if rc != 0 or not note.startswith("gitvow-session"):
        return {}
    try:
        return json.loads(note.split("\n", 1)[1])
    except (ValueError, IndexError):
        return {}


# ---------------------------------------------------------------------------------------------------------------
# parts
# ---------------------------------------------------------------------------------------------------------------


def decision_rows(cwd: str, source: str, since: str | None) -> tuple[list[dict], list[dict], list[dict]]:
    """(decisions, observed, sessions) drawn from the trailers and the session notes."""
    decisions, observed, sessions = [], [], []
    for sha, day, body in _log(cwd, since):
        if "Gitvow-" not in body:
            continue
        note = _session_note(cwd, sha, body)
        by_finding = {d.get("finding"): d for d in (note.get("decisions") or []) if isinstance(d, dict)}
        sid = (SESSION_RE.search(body) or [None, None])[1] if SESSION_RE.search(body) else None
        for t in parse_trailers(body):
            n = by_finding.get(t["finding"]) or {}
            base = {
                "source": source,
                "sha": sha,
                "date": day,
                "finding": t["finding"],
                "kind": n.get("kind"),
                "path": n.get("path"),
                "session_id": sid,
                "class": None,  # not computed by this version; the store derives from kind + reason
                "reason": n.get("reason"),
            }
            if t["answer"] == "observed":
                observed.append(base)
                continue
            decisions.append(
                {
                    **base,
                    "answer": t["answer"],
                    "by": t.get("by"),
                    "authority": n.get("authority"),
                    "scope": t.get("scope"),
                    "to": t.get("to"),
                    "note": t.get("note"),
                    "human_turns_after_card": n.get("human_turns_after_card"),
                    "proposed": n.get("proposed"),
                    "binding": "note" if n else "trivial",
                }
            )
        if note and sid:
            att = note.get("attribution") or {}
            usage = note.get("usage") or {}
            sessions.append(
                {
                    "source": source,
                    "sha": sha,
                    "date": day,
                    "session_id": sid,
                    "step": note.get("step"),
                    "agent": note.get("agent"),
                    "model": (usage.get("models") or [None])[0] if isinstance(usage.get("models"), list) else None,
                    "tool_calls": note.get("tool_calls_so_far"),
                    "assistant_turns": note.get("assistant_turns_so_far"),
                    "files_written": len(note.get("files_written_by_agent_this_session") or []),
                    "attribution": {
                        k: att.get(k)
                        for k in ("files_in_commit", "touched_by_agent", "lines_added_in_commit", "agent_share")
                    },
                    "usage": {
                        k: usage.get(k) for k in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost_usd")
                    },
                    "edits_outside_repository": {
                        k: {"count": v.get("count", 0)} for k, v in (note.get("edits_outside_repository") or {}).items()
                    },
                    "last_stated_plan": None,  # the `plans` sub-class is off by default; never exported here
                }
            )
    return decisions, observed, sessions


def claim_rows(cwd: str, source: str) -> list[dict]:
    rows = []
    for c in confirmed_and_rejected(cwd):
        if c["verdict"] != "confirmed":
            continue
        rows.append(
            {
                "source": source,
                "sha": c["sha"],
                "date": c["date"],
                "kind": "claim",
                "claim_id": c["claim_id"],
                "verdict": "confirmed",
                "by": c.get("by"),
                "authority": c.get("authority"),
                "paths": c.get("paths") or [],
                "reach": "repo",
                "text": c.get("text"),
                "edited": bool(c.get("edited")),
                "speaker": c.get("speaker"),
            }
        )
    return rows


def rule_verdict_rows(cwd: str, source: str) -> list[dict]:
    rows = []
    for v in rule_decisions(cwd):
        rc, note, _ = git(["notes", f"--ref={RULES_NOTES_REF}", "show", v["sha"]], cwd)
        evidence: dict[str, Any] = {}
        if rc == 0 and note.startswith(RULES_NOTE_HEADER):
            try:
                evidence = json.loads(note.split("\n", 1)[1])
            except (ValueError, IndexError):
                evidence = {}
        rows.append(
            {
                "source": source,
                "sha": v["sha"],
                "date": v.get("date"),
                "finding": v["finding"],
                "verdict": v["verdict"],
                "by": v.get("by"),
                "answer": v.get("answer"),
                "note": v.get("note"),
                "authority": evidence.get("authority"),
                "threshold": evidence.get("threshold"),
                "evidence_run": [
                    {
                        k: r.get(k)
                        for k in ("finding", "answer", "count", "first", "last", "by", "exceptions", "expires")
                    }
                    for r in (evidence.get("rules") or [])
                    if isinstance(r, dict)
                ],
            }
        )
    return rows


def gate_event_rows(cwd: str, source: str, since: str | None) -> list[dict]:
    """Counts per session per kind per reason from the local hook log. Never a command, never a path."""
    gd = git_dir(cwd)
    p = os.path.join(gd, "gitvow-hooks.log") if gd else None
    if not p or not os.path.exists(p):
        return []
    counts: dict[tuple[str, str, str], int] = {}
    with open(p) as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if since and str(ev.get("ts", ""))[:10] < since:
                continue
            kind = str(ev.get("kind") or "")
            if kind not in (
                "blocked",
                "confirm_required",
                "finding",
                "observed",
                "card",
                "card_ack",
                "allowed_by_session_answer",
            ):
                continue
            key = (str(ev.get("session_id") or ""), kind, str(ev.get("reason") or "")[:80])
            counts[key] = counts.get(key, 0) + 1
    return [
        {"source": source, "session_id": s, "kind": k, "reason": r, "count": n}
        for (s, k, r), n in sorted(counts.items())
    ]


def meter_rows(cwd: str, source: str, since: str | None) -> dict:
    """Agent-authored change volume from the remote's own history: a floor, and it says so."""
    args = ["log", "--numstat", "--format=%x01%H%x00%ad%x00%B%x02", "--date=short", "--no-merges"]
    if since:
        args.append(f"--since={since}")
    rc, out, _ = git(args, cwd)
    commits = agent_commits = added = removed = 0
    if rc == 0:
        for rec in out.split("\x01"):
            if not rec.strip():
                continue
            head, _, stats = rec.partition("\x02")
            parts = head.split("\x00", 2)
            if len(parts) < 3:
                continue
            body = parts[2]
            commits += 1
            if not any(rx.search(body) for _, rx in AGENT_MARKS):
                continue
            agent_commits += 1
            for ln in stats.splitlines():
                cols = ln.split("\t")
                if len(cols) >= 2:
                    added += int(cols[0]) if cols[0].isdigit() else 0
                    removed += int(cols[1]) if cols[1].isdigit() else 0
    return {
        "source": source,
        "window": {"from": since, "to": date.today().isoformat()},
        "commits": commits,
        "agent_signed_commits": agent_commits,
        "agent_lines_added": added,
        "agent_lines_removed": removed,
        "floor": True,
        "marks": [name for name, _ in AGENT_MARKS],
    }


# ---------------------------------------------------------------------------------------------------------------
# redaction, canonical form, the bundle
# ---------------------------------------------------------------------------------------------------------------


def _strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


_IDENTIFIER = re.compile(r"^(clm_[0-9A-Z]{26}|[0-9a-f]{7,64}|[0-9a-fA-F-]{8,36}|sha256:[0-9a-f]{64})$")


def verify_redaction(part: str, rows: list[dict], rules: list[tuple[str, str]] | None) -> tuple[list[dict], list[dict]]:
    """Rows whose strings the redaction rules would change are refused, never rewritten.

    Identifiers the record itself mints (claim ids, commit shas, session ids, digests) look like high-entropy
    tokens to the redactor and are skipped; everything else, including every free-text field, is checked.
    """
    if rules is None:
        return rows, []
    kept, refused = [], []
    for i, r in enumerate(rows):
        hit = None
        for s in _strings(r):
            if _IDENTIFIER.match(s):
                continue
            if redact(s, rules) != s:
                hit = s
                break
        if hit is None:
            kept.append(r)
        else:
            refused.append(
                {"part": PART_FILES[part], "row": i, "field_digest": hashlib.sha256(hit.encode()).hexdigest()[:12]}
            )
    return kept, refused


def canonical(rows: list[dict] | dict) -> bytes:
    if isinstance(rows, dict):
        return (json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    ordered = sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))
    return "".join(
        json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n" for r in ordered
    ).encode()


def build(
    cwd: str, since: str | None = "90d", override: list[str] | None = None, rules: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    """Assemble the bundle in memory: parts, manifest, attestation. Nothing is written."""
    top = toplevel(cwd)
    if not top:
        raise ValueError("not inside a git repository")
    cons = consent(cwd, override)
    source = remote_name(top)
    since_d = since_date(since)
    decisions, observed, sessions = decision_rows(top, source, since_d)
    parts: dict[str, Any] = {}
    if cons["decisions"]:
        parts["decisions"] = decisions
    if cons["observed"]:
        parts["observed"] = observed
    if cons["claims"]:
        parts["claims"] = claim_rows(top, source)
    if cons["rule_verdicts"]:
        parts["rule_verdicts"] = rule_verdict_rows(top, source)
    if cons["sessions"]:
        parts["sessions"] = sessions
    if cons["gate_events"]:
        parts["gate_events"] = gate_event_rows(top, source, since_d)
    if cons["meter"]:
        parts["meter"] = meter_rows(top, source, since_d)
    refused_all: list[dict] = []
    for name, rows in list(parts.items()):
        if isinstance(rows, list):
            kept, refused = verify_redaction(name, rows, rules)
            parts[name] = kept
            refused_all += refused
    _, head, _ = git(["rev-parse", "HEAD"], top)
    rc, refs, _ = git(["for-each-ref", "--format=%(refname) %(objectname)", "refs/notes/gitvow/"], top)
    notes = dict(line.split(" ", 1) for line in refs.splitlines() if " " in line) if rc == 0 else {}
    manifest_parts = []
    for name in CLASSES:
        if name not in parts:
            continue
        data = canonical(parts[name])
        manifest_parts.append(
            {
                "path": PART_FILES[name],
                "class": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "rows": len(parts[name]) if isinstance(parts[name], list) else 1,
                "bytes": len(data),
            }
        )
    digest = hashlib.sha256("".join(p["sha256"] for p in manifest_parts).encode()).hexdigest()
    manifest = {
        "schema": SCHEMA,
        "kind": KIND,
        "gitvow": __version__,
        "source": {"remote": source},
        "frontier": {
            "head": head,
            "notes": notes,
            "since": since_d,
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "consent": cons,
        "parts": manifest_parts,
        "digest": f"sha256:{digest}",
        "idempotency_key": f"sha256:{digest}",
    }
    attestation = {
        "schema": SCHEMA,
        "redaction": "re-verified at export with the repository's rules; rows that tripped a rule were refused, not rewritten",
        "rules_digest": hashlib.sha256(json.dumps(rules or [], sort_keys=True).encode()).hexdigest()
        if rules is not None
        else None,
        "refused": refused_all,
        "never_carried": [
            "transcripts",
            "working trees",
            "snapshots",
            "command lines",
            "prompts",
            "person-reach claims",
        ],
        "signature": None,
    }
    return {"parts": parts, "manifest": manifest, "attestation": attestation, "top": top}


def write(bundle: dict[str, Any], out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in CLASSES:
        if name not in bundle["parts"]:
            continue
        p = os.path.join(out_dir, PART_FILES[name])
        with open(p, "wb") as fh:
            fh.write(canonical(bundle["parts"][name]))
        written.append(p)
    for name in ("manifest", "attestation"):
        p = os.path.join(out_dir, f"{name}.json")
        with open(p, "w") as fh:
            json.dump(bundle[name], fh, indent=1, sort_keys=True)
        written.append(p)
    return written


def render_why(bundle: dict[str, Any]) -> str:
    m, a = bundle["manifest"], bundle["attestation"]
    lines = [
        f"bundle for {m['source']['remote']} at {m['frontier']['head'][:12]}"
        + (f", since {m['frontier']['since']}" if m["frontier"]["since"] else ""),
        f"digest {m['digest'][:19]}…  (the idempotency key: sending the same bundle twice changes nothing)",
        "",
        "what leaves, one file per consented class:",
    ]
    for p in m["parts"]:
        lines.append(f"  {p['path']:22s} {p['rows']:5d} rows  {p['bytes']:8d} bytes  sha256 {p['sha256'][:12]}")
    off = [c for c, v in m["consent"].items() if not v]
    if off:
        lines.append(f"  not consented, no file: {', '.join(off)}")
    lines += ["", "what never leaves: " + ", ".join(a["never_carried"])]
    if a["refused"]:
        lines.append(f"refused by redaction re-verification: {len(a['refused'])} row(s), listed in attestation.json")
    else:
        lines.append("redaction re-verified: no row refused")
    lines.append("consent is the committed .gitvow/export.json (or the defaults); --consent overrides it for one run")
    return "\n".join(lines) + "\n"
