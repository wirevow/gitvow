"""Organisation rule packs and the cached brief: what a store pushes down, read from a local cache.

A **pack** is a set of policy rules and settings a named person accepted for the organisation, published by the
store per repository at `packs/<source>.json`. `gitvow sync` copies it to `~/.gitvow/cache/packs/<source>.json`
and `load_policy` applies it as a layer under the repository's own policy: pack rules are added, and pack
settings may tighten the repository (open → strict, session scope off, a higher rule threshold) but never loosen
it. A pack that is missing, expired or malformed changes nothing; the store fails open, the gate keeps the
policy it had.

The **brief** is the store's answer to "what does the organisation's record say about this repository": standing
per finding class with recall from other repositories, accepted rules, confirmed claims, and gaps. It is cached at
`~/.gitvow/cache/brief/<source>.json`, handed to the agent at session start with its age, and marked stale when
older than the store said it would stay fresh. Context, never permission: it never answers the card.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
import re
from typing import Any

from .policy import PolicyError, _validate, rule_pattern

PACK_SCHEMA = 1
BRIEF_PROTOCOL = 1
CACHE = os.path.join(".gitvow", "cache")


def _home(home: str | None) -> str:
    return home or os.path.expanduser("~")


def source_dir(source: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", source)


def source_of(cwd: str) -> str:
    from .export import remote_name

    return remote_name(cwd)


def pack_path(home: str | None, source: str) -> str:
    return os.path.join(_home(home), CACHE, "packs", f"{source_dir(source)}.json")


def brief_path(home: str | None, source: str) -> str:
    return os.path.join(_home(home), CACHE, "brief", f"{source_dir(source)}.json")


def meta_path(path: str) -> str:
    return path[: -len(".json")] + ".meta.json"


def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------------------------------------------
# packs
# ---------------------------------------------------------------------------------------------------------------


def validate_pack(pk: Any, path: str) -> None:
    if not isinstance(pk, dict) or pk.get("pack") != PACK_SCHEMA:
        raise PolicyError(f'{path}: not a pack (expected "pack": {PACK_SCHEMA})')
    names = pk.get("from") or ([pk["name"]] if pk.get("name") else [])
    if not names or not all(isinstance(n, str) and n for n in names):
        raise PolicyError(f"{path}: a pack needs 'from' (the packs it merges) or 'name'")
    if not isinstance(pk.get("rules"), dict):
        raise PolicyError(f"{path}: 'rules' must be an object of policy lists")
    for key in pk["rules"]:
        if key not in ("bash_deny", "bash_confirm", "path_confirm"):
            raise PolicyError(f"{path}: a pack may carry bash_deny, bash_confirm and path_confirm only, not {key}")
    if not isinstance(pk.get("expires"), str):
        raise PolicyError(f"{path}: 'expires' (ISO date) is required; a pack without a decay date binds forever")
    try:
        dt.date.fromisoformat(pk["expires"][:10])
    except ValueError as e:
        raise PolicyError(f"{path}: bad 'expires' {pk['expires']!r}") from e
    settings = pk.get("settings") or {}
    if not isinstance(settings, dict) or not isinstance(settings.get("decisions", {}), dict):
        raise PolicyError(f"{path}: 'settings.decisions' must be an object")
    _validate({**pk["rules"], "decisions": settings.get("decisions", {})}, path)


def load_pack(cwd: str, home: str | None = None, today: dt.date | None = None) -> dict[str, Any] | None:
    """The cached pack for this repository, or None when there is none. A pack that cannot be used comes back
    with `error` or `expired` set and is never applied."""
    p = pack_path(home, source_of(cwd))
    if not os.path.exists(p):
        return None
    try:
        pk = _read(p)
        validate_pack(pk, p)
    except (OSError, ValueError, PolicyError) as e:
        return {"path": p, "error": str(e)[:300], "pack": PACK_SCHEMA}
    pk["path"] = p
    pk["names"] = pk.get("from") or [pk["name"]]
    if dt.date.fromisoformat(pk["expires"][:10]) < (today or dt.date.today()):
        pk["expired"] = True
    return pk


def _rule_key(key: str, r: dict[str, Any]) -> str:
    return r["pattern"] if key == "path_confirm" else rule_pattern(r)


def apply(pol: dict[str, Any], pk: dict[str, Any] | None) -> dict[str, Any]:
    """The policy with the pack applied: rules added (tagged with the pack they came from), settings tightened,
    never loosened. Returns a new dict; `_pack` records what happened for `gitvow pack` and the card."""
    if pk is None:
        return pol
    out = copy.deepcopy(pol)
    if pk.get("error") or pk.get("expired"):
        out["_pack"] = {k: pk.get(k) for k in ("path", "error", "expired", "names", "expires")}
        out["_pack"]["applied"] = False
        return out
    added = 0
    for key in ("bash_deny", "bash_confirm", "path_confirm"):
        have = {_rule_key(key, r) for r in out.get(key, [])}
        for r in pk["rules"].get(key, []):
            if _rule_key(key, r) in have:
                continue
            out.setdefault(key, []).append({**r, "pack": r.get("pack") or ", ".join(pk["names"])})
            added += 1
    dec = out.setdefault("decisions", {})
    want = (pk.get("settings") or {}).get("decisions") or {}
    tightened: list[str] = []
    if want.get("mode") == "strict" and dec.get("mode", "open") != "strict":
        dec["mode"] = "strict"
        tightened.append("mode=strict")
    if want.get("session_scope") is False and dec.get("session_scope", True) is not False:
        dec["session_scope"] = False
        tightened.append("session_scope=false")
    thr = want.get("rule_threshold")
    if isinstance(thr, int) and not isinstance(thr, bool) and thr > dec.get("rule_threshold", 3):
        dec["rule_threshold"] = thr
        tightened.append(f"rule_threshold={thr}")
    _validate(out, pk.get("path") or "pack")
    out["_pack"] = {
        "applied": True,
        "names": pk["names"],
        "rules": added,
        "tightened": tightened,
        "accepted_by": pk.get("accepted_by"),
        "expires": pk["expires"],
        "as_of": pk.get("as_of"),
        "digest": pk.get("digest"),
        "path": pk.get("path"),
    }
    return out


def render_pack(pol: dict[str, Any], for_agent: bool = True) -> str:
    """The pack as context: which rules the organisation put here, who accepted them, when they lapse."""
    info = pol.get("_pack")
    if not info:
        return (
            ""
            if for_agent
            else "No organisation pack cached for this repository (gitvow sync fetches one from a store).\n"
        )
    if not info.get("applied"):
        why = "expired on " + str(info.get("expires")) if info.get("expired") else str(info.get("error"))
        return (
            ""
            if for_agent
            else f"Organisation pack {', '.join(info.get('names') or ['?'])} NOT applied: {why}\n  {info.get('path')}\n"
        )
    who = f", accepted by {info['accepted_by']}" if info.get("accepted_by") else ""
    head = f"Organisation rules in force (pack {', '.join(info['names'])}{who}, lapses {info['expires'][:10]})"
    lines = [f"## {head}" if for_agent else head, ""] if for_agent else [head]
    n = 0
    for key, act in (("bash_deny", "deny"), ("bash_confirm", "ask"), ("path_confirm", "ask")):
        for r in pol.get(key, []):
            if not r.get("pack"):
                continue
            n += 1
            what = r["pattern"] if "pattern" in r else f"{r['program']} {'|'.join(r['verbs'])}"
            tier = "" if act == "deny" else f" at {r.get('when', 'immediate' if key == 'bash_confirm' else 'commit')}"
            lines.append(f"- {act}{tier}: {r.get('reason', what)}" + (f" (`{what}`)" if not for_agent else ""))
    if info.get("tightened"):
        lines.append(
            f"- settings set by the pack: {', '.join(info['tightened'])}. This repository may tighten them, not loosen them."
        )
    if for_agent:
        lines += [
            "",
            "Context, not permission: a pack rule is what a named person accepted for the organisation; the gate still asks.",
        ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# the brief
# ---------------------------------------------------------------------------------------------------------------


def load_brief(cwd: str, home: str | None = None, now: dt.datetime | None = None) -> dict[str, Any] | None:
    """The cached brief for this repository with `source: cache` and its age, or None. Stale when older than the
    store's `stale_after`; a stale brief is still handed over, saying so, rather than replaced by silence."""
    p = brief_path(home, source_of(cwd))
    if not os.path.exists(p):
        return None
    try:
        b = _read(p)
    except (OSError, ValueError):
        return None
    if not isinstance(b, dict) or b.get("protocol") != BRIEF_PROTOCOL:
        return None
    now = now or dt.datetime.now(dt.timezone.utc)
    b["source"] = "cache"
    try:
        as_of = dt.datetime.strptime(b["as_of"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        age = max(0.0, (now - as_of).total_seconds() / 3600)
    except (KeyError, ValueError):
        age = None
    b["age_hours"] = round(age, 1) if age is not None else None
    stale_after = b.get("stale_after")
    try:
        stale = stale_after is not None and now > dt.datetime.strptime(stale_after, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError:
        stale = True
    if stale:
        b.setdefault("gaps", []).append({"kind": "stale", "age_hours": b["age_hours"]})
    b["stale"] = bool(stale)
    return b


def render_brief(b: dict[str, Any], for_agent: bool = True) -> str:
    age = f"{b['age_hours']:g} h old" if b.get("age_hours") is not None else "age unknown"
    head = f"What the organisation's record says about {b.get('repo', 'this repository')} (store cache, {age}{', STALE' if b.get('stale') else ''})"
    lines = [f"## {head}", ""] if for_agent else [head]
    for c in b.get("classes", []):
        s = c.get("standing", {})
        answer = s.get("answer") or "no unscoped answer"
        where = f", also in {', '.join(c['elsewhere'])}" if c.get("elsewhere") else ""
        line = f"- {c['class']}: {answer} x{s.get('count', 0)} in {s.get('repos', 0)} repo(s){where}; observed {s.get('observed', 0)}"
        if s.get("exceptions"):
            line += f"; exceptions {', '.join(s['exceptions'])}"
        if c.get("rule"):
            line += f"; rule accepted by {c['rule'].get('accepted_by')} on {c['rule'].get('accepted_on')}"
        lines.append(line)
    if b.get("claims"):
        lines.append(f"- confirmed claims on record elsewhere in the store: {len(b['claims'])}")
    if b.get("gaps"):
        lines.append(
            "- gaps: " + "; ".join(f"{g['kind']} {g.get('class') or g.get('path') or ''}".strip() for g in b["gaps"])
        )
    if for_agent:
        lines += [
            "",
            "Context, not permission: standing is an observation about the past, a rule is what a named person "
            "accepted, a claim is what a person said and confirmed. None of it answers the card.",
        ]
    return "\n".join(lines) + "\n"


def context(cwd: str, home: str | None, pol: dict[str, Any]) -> str:
    """Pack and brief as SessionStart context. Empty when neither is cached."""
    out = render_pack(pol, for_agent=True) if pol.get("_pack", {}).get("applied") else ""
    b = load_brief(cwd, home)
    if b and (b.get("classes") or b.get("claims") or b.get("gaps")):
        out += ("\n" if out else "") + render_brief(b, for_agent=True)
    return out
