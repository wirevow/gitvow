"""Runtime facts at the gate: what the store's brief says about a route in the file the agent is editing.

The brief's `facts[]` (contract B, amended 2026-10-12) carries observations the runtime facts provider wrote:
per route, requests over a window, latency, error rate, coverage, an `as_of` and a ref. The gate reads them from
the local cache and nothing else, never the network, and raises two findings of its own:

  silent route    an edit to a file that carries a route measured at zero requests over the window: an
                  observe-tier finding by default, so the record shows how often dead surface gets touched
  erroring route  an edit to a file whose route's 5xx rate is at or above the threshold: a commit-tier finding
                  by default, so a person answers before the change lands

Both carry `observations: [ref]`, which rides into the decision, the note and the export, so the question and
the answer point at the same fact. A fact older than `runtime.max_age_hours` is not acted on; the brief still
shows it with its age.
"""

from __future__ import annotations

import os
from typing import Any

from .pack import load_brief
from .policy import Decision

DEFAULTS: dict[str, Any] = {
    "drift": "commit",  # commit | immediate | observe | off: the service a file declares is not running as declared
    "silent_route": "observe",  # observe | commit | immediate | off
    "error_rate_confirm": 0.01,  # 5xx share at or above which an edit to the route's file is asked about
    "erroring_route": "commit",  # commit | immediate | observe | off
    "max_age_hours": 168,  # a fact older than a week is shown, not acted on
}
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def settings(pol: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for k, v in ((pol or {}).get("runtime") or {}).items():
        if k in DEFAULTS:
            out[k] = v
    return out


def _rel(path: str, cwd: str) -> str:
    if os.path.isabs(path):
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(cwd))
        return rel if not rel.startswith("..") else path
    return path


def facts_for_path(cwd: str, home: str | None, rel_path: str) -> list[dict[str, Any]]:
    """The cached brief's facts whose `path` is this file. Empty when there is no brief or no facts."""
    b = load_brief(cwd, home)
    if not b:
        return []
    return [f for f in b.get("facts") or [] if f.get("path") == rel_path]


def findings_from_facts(
    cwd: str, home: str | None, tool: str, tool_input: dict[str, Any], pol: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Findings raised by the runtime facts on the file being edited. Each names the observation it rests on."""
    if tool not in EDIT_TOOLS:
        return []
    path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
    if not path:
        return []
    cfg = settings(pol)
    rel = _rel(path, cwd)
    silent: list[dict[str, Any]] = []
    erroring: list[dict[str, Any]] = []
    drifting: list[dict[str, Any]] = []
    for f in facts_for_path(cwd, home, rel):
        if f.get("coverage") != "measured":
            continue
        age = f.get("age_hours")
        if age is not None and cfg["max_age_hours"] and age > float(cfg["max_age_hours"]):
            continue
        if f.get("reopened"):
            continue  # a deploy landed after this observation: shown in the brief, not acted on until re-observed
        facts = f.get("facts") or {}
        if f.get("subject_type") == "service":
            if facts.get("drift") and cfg["drift"] != "off":
                drifting.append(f)
            continue
        if facts.get("requests") == 0 and cfg["silent_route"] != "off":
            silent.append(f)
        rate = facts.get("error_rate_5xx")
        thr = float(cfg["error_rate_confirm"] or 0)
        if rate is not None and thr and float(rate) >= thr and cfg["erroring_route"] != "off":
            erroring.append(f)
    # one finding per file and kind, never one per route: a controller that carries twenty sleeping routes is one
    # question, with every route and every ref on it
    out: list[dict[str, Any]] = []
    if silent:
        window = silent[0].get("window") or "the window"
        head = silent[0]
        subject = head.get("subject") or "route"
        text = (
            f"edit {rel} (route {subject} silent over {window})"
            if len(silent) == 1
            else f"edit {rel} ({len(silent)} routes silent over {window})"
        )
        out.append(
            {
                "finding": text,
                "kind": "edit",
                "subject": rel,
                "path": rel,
                "reason": f"route silent over {window}"
                if len(silent) == 1
                else f"{len(silent)} routes silent over {window}",
                "evidence": _evidence(silent),
                "observations": [f["ref"] for f in silent if f.get("ref")],
                "tier": cfg["silent_route"],
            }
        )
    if erroring:
        worst = max(erroring, key=lambda f: float((f.get("facts") or {}).get("error_rate_5xx") or 0))
        rate = float((worst.get("facts") or {}).get("error_rate_5xx") or 0)
        window = worst.get("window") or "the window"
        subject = worst.get("subject") or "route"
        text = (
            f"edit {rel} (route {subject} erroring, 5xx {100 * rate:.1f}%)"
            if len(erroring) == 1
            else f"edit {rel} ({len(erroring)} routes erroring, worst {subject} 5xx {100 * rate:.1f}%)"
        )
        out.append(
            {
                "finding": text,
                "kind": "edit",
                "subject": rel,
                "path": rel,
                "reason": f"route erroring: 5xx {100 * rate:.1f}% over {window}"
                if len(erroring) == 1
                else f"{len(erroring)} routes erroring over {window}, worst 5xx {100 * rate:.1f}%",
                "evidence": _evidence(
                    sorted(erroring, key=lambda f: -float((f.get("facts") or {}).get("error_rate_5xx") or 0))
                ),
                "observations": [f["ref"] for f in erroring if f.get("ref")],
                "tier": cfg["erroring_route"],
            }
        )
    for f in drifting:
        # the blueprint's observation: the file declares a service the cluster is not running as declared
        reasons = [str(d) for d in (f.get("facts") or {}).get("drift") or []]
        out.append(
            {
                "finding": f"edit {rel} (service {f.get('subject')} drifting: {reasons[0]})"
                if len(reasons) == 1
                else f"edit {rel} (service {f.get('subject')} drifting: {reasons[0]} and {len(reasons) - 1} more)",
                "kind": "edit",
                "subject": rel,
                "path": rel,
                "reason": "the service this file declares is not running as declared",
                "evidence": [*reasons[:4], *_evidence([f])],
                "observations": [f["ref"]] if f.get("ref") else [],
                "tier": cfg["drift"],
            }
        )
    return out


def _evidence(fs: list[dict[str, Any]], limit: int = 4) -> list[str]:
    """One line per route with its numbers, then the refs, then the reopen condition. Capped: the card shows four."""
    out: list[str] = []
    for f in fs[:limit]:
        where = f" ({f.get('service')}, {f.get('env')})" if f.get("service") else ""
        out.append(f"{f.get('subject')}: {f.get('answer')}, as of {f.get('as_of')}{where}")
    if len(fs) > limit:
        out.append(f"and {len(fs) - limit} more route(s) in this file")
    refs = [f["ref"] for f in fs if f.get("ref")]
    if refs:
        out.append(
            "observation " + ", ".join(refs[:limit]) + (f" and {len(refs) - limit} more" if len(refs) > limit else "")
        )
    if fs and fs[0].get("reopen"):
        out.append("reopens on: " + "; ".join(fs[0]["reopen"]))
    return out


def merge(d: Decision, extra: list[dict[str, Any]], detail: str) -> Decision:
    """Fold fact findings into the policy's decision. A deny stays a deny. An immediate fact finding becomes an
    immediate confirm when nothing else blocked. Otherwise the findings join the deferred or observed set."""
    if not extra or d.outcome == "deny":
        return d
    if d.blocks:
        return d
    immediate = [f for f in extra if f.get("tier") == "immediate"]
    if immediate:
        f = dict(immediate[0])
        return Decision("confirm", f["reason"], detail, "immediate", (f,))
    for f in extra:
        f["observe"] = f.get("tier") == "observe"
    findings = list(d.findings) + extra
    when = "observe" if all(f.get("observe") for f in findings) else "commit"
    reason = "; ".join(f["reason"] for f in findings[:3])
    return Decision("confirm", reason, d.detail or detail, when, tuple(findings))
