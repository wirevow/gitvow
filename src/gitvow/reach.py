"""Reach: who owns the path a finding is about.

"Who decides about this?" had no answer inside the tool: authority was a list in the policy file, a referral named
whoever the person typed, and a finding on a path nobody owns looked exactly like one on a path someone does.
This module resolves the owner of a path from the ownership file the repository already keeps (CODEOWNERS, in
the places GitHub and GitLab look), read at HEAD and recorded with the commit that last changed it, with the policy's
`decisions.authorities` as the fallback. Every finding that names a path carries its owner from the moment it is
raised; a path with no owner is *unowned surface*, marked on the finding and counted by the digest.

Matching follows the CODEOWNERS rules: last matching line wins; `*` does not cross `/`; `**` does; a pattern
with a slash anywhere but the end is anchored to the repository root, one without a slash matches a file or
directory of that name anywhere; a trailing `/` names a directory and everything under it; a line with a
pattern and no owner makes the path explicitly unowned. GitLab section headers are skipped. Owners are kept as
written (`@team`, `@person`, an email): no forge is asked, no team is expanded.
"""

from __future__ import annotations

import re
from typing import Any

from .state import git, toplevel

OWNERS_FILES = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS", ".gitlab/CODEOWNERS")


def load_owners(cwd: str, rev: str = "HEAD") -> dict[str, Any] | None:
    """The first ownership file present at `rev`, parsed: path, commit, rules [(pattern, owners, regex)]."""
    top = toplevel(cwd) or cwd
    for name in OWNERS_FILES:
        rc, text, _ = git(["show", f"{rev}:{name}"], top)
        if rc == 0:
            # the commit that last changed the file, so "owners as of <commit>" names the ownership, not the tip
            rc2, sha, _ = git(["log", "-1", "--format=%H", rev, "--", name], top)
            if rc2 != 0 or not sha:
                _, sha, _ = git(["rev-parse", rev], top)
            return {"path": name, "commit": sha[:12], "rules": parse(text)}
    return None


def parse(text: str) -> list[dict[str, Any]]:
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):  # comment, or a GitLab section header
            continue
        parts = _split(line)
        if not parts:
            continue
        pattern, owners = parts[0], [o for o in parts[1:] if not o.startswith("#")]
        try:
            rx = compile_pattern(pattern)
        except re.error:
            continue
        rules.append({"pattern": pattern, "owners": owners, "regex": rx})
    return rules


def _split(line: str) -> list[str]:
    """Whitespace-separated, honouring `\\ ` inside a pattern."""
    out: list[str] = []
    cur = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == " ":
            cur += " "
            i += 2
            continue
        if ch.isspace():
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
        i += 1
    if cur:
        out.append(cur)
    return out


def compile_pattern(pattern: str) -> re.Pattern[str]:
    p = pattern
    directory = p.endswith("/")
    p = p.rstrip("/")
    anchored = p.startswith("/") or "/" in p
    p = p.lstrip("/")
    body = ""
    i = 0
    while i < len(p):
        ch = p[i]
        if ch == "*":
            if p[i : i + 3] == "**/" and (i == 0 or p[i - 1] == "/"):
                body += "(?:.*/)?"  # `a/**/b` also matches `a/b`
                i += 3
                continue
            if p[i : i + 2] == "**":
                body += ".*"
                i += 2
                continue
            body += "[^/]*"
        elif ch == "?":
            body += "[^/]"
        else:
            body += re.escape(ch)
        i += 1
    head = "^" if anchored else "^(?:.*/)?"
    tail = "(?:/.*)?$"  # a match on a directory covers everything under it; on a file, the file
    if directory:
        tail = "/.*$" if body else ".*$"
    return re.compile(head + body + tail)


def match(rules: list[dict[str, Any]], rel: str) -> dict[str, Any] | None:
    """The last matching rule for a repository-relative path, or None."""
    rel = rel.lstrip("/")
    hit = None
    for r in rules:
        if r["regex"].search(rel):
            hit = r
    return hit


def resolve(
    cwd: str, rel: str, pol: dict[str, Any] | None = None, owners: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Owner record for a path: owners, source, pattern, commit. `owners` may be a preloaded `load_owners()`."""
    o = owners if owners is not None else load_owners(cwd)
    if o:
        hit = match(o["rules"], rel)
        if hit is not None:
            return {
                "owners": list(hit["owners"]),
                "source": f"{o['path']}@{o['commit']}",
                "pattern": hit["pattern"],
                "unowned": not hit["owners"],
            }
    auth = list(((pol or {}).get("decisions") or {}).get("authorities") or [])
    if auth:
        return {"owners": auth, "source": "policy.authorities", "pattern": None, "unowned": False}
    return {
        "owners": [],
        "source": f"{o['path']}@{o['commit']}" if o else None,
        "pattern": None,
        "unowned": True,
    }


def attach(cwd: str, findings: list[dict[str, Any]], pol: dict[str, Any] | None = None) -> None:
    """Set `owner` on each finding that names a path. Command findings have no path and get None."""
    owners = load_owners(cwd)
    for f in findings:
        path = f.get("path") or ""
        f["owner"] = resolve(cwd, path, pol, owners) if path else None


def card_line(f: dict[str, Any]) -> str | None:
    o = f.get("owner")
    if not o:
        return None
    if o["owners"]:
        via = f" ({o['source']}" + (f", {o['pattern']}" if o.get("pattern") else "") + ")"
        return f"   owner: {' '.join(o['owners'])}{via}"
    where = f"; {o['source']} has no rule for it" if o.get("source") else "; no ownership file and no authorities"
    return f"   owner: none (unowned surface{where})"


def summary(cwd: str, pol: dict[str, Any] | None = None, since_day: str | None = None) -> dict[str, Any]:
    """Decisions in history resolved against the ownership file at HEAD: share with an owner, unowned paths."""
    from . import decisions as dec

    top = toplevel(cwd) or cwd
    owners = load_owners(top)
    rc, out, _ = git(
        ["log", "-5000", "--format=%H%x00%ad%x00%B%x01", "--date=short"]
        + ([f"--since={since_day}"] if since_day else []),
        top,
    )
    with_path = resolved = 0
    unowned: dict[str, int] = {}
    owners_seen: dict[str, int] = {}
    if rc == 0:
        for rec in out.split("\x01"):
            rec = rec.strip("\n")
            if "Gitvow-" not in rec:
                continue
            _sha, _date, body = rec.split("\x00", 2)
            for t in dec.parse_trailers(body):
                path = _path_of(t["finding"])
                if not path:
                    continue
                with_path += 1
                o = resolve(top, path, pol, owners)
                if o["owners"]:
                    resolved += 1
                    for name in o["owners"]:
                        owners_seen[name] = owners_seen.get(name, 0) + 1
                else:
                    unowned[path] = unowned.get(path, 0) + 1
    return {
        "owners_file": f"{owners['path']}@{owners['commit']}" if owners else None,
        "rules": len(owners["rules"]) if owners else 0,
        "decided_paths": with_path,
        "resolved": resolved,
        "unowned_paths": len(unowned),
        "unowned": sorted(unowned.items(), key=lambda kv: (-kv[1], kv[0]))[:20],
        "owners": sorted(owners_seen.items(), key=lambda kv: (-kv[1], kv[0]))[:20],
    }


_PATH_RE = re.compile(r"^(?:edit (\S+)|remove route \S+ from (\S+)|route \S+ in (\S+))$")


def _path_of(finding: str) -> str | None:
    """The path a finding text names: `edit <path>`, `route /x in <path>`, `remove route /x from <path>`. Commands name none."""
    m = _PATH_RE.match(finding.split(" @ ")[0].strip())
    if not m:
        return None
    return next(g for g in m.groups() if g)


def summary_line(s: dict[str, Any]) -> str:
    if not s.get("decided_paths"):
        return ""
    line = f"Reach: {s['resolved']} of {s['decided_paths']} decided path{'s' if s['decided_paths'] != 1 else ''} have an owner"
    if s.get("unowned_paths"):
        line += f" · {s['unowned_paths']} unowned path{'s' if s['unowned_paths'] != 1 else ''} carry decisions"
    line += f" · owners from {s['owners_file']}" if s.get("owners_file") else " · no ownership file"
    return line


def render(s: dict[str, Any]) -> str:
    lines = []
    if s.get("owners_file"):
        lines.append(f"ownership file: {s['owners_file']}, {s['rules']} rule{'s' if s['rules'] != 1 else ''}")
    else:
        lines.append("no ownership file at HEAD (CODEOWNERS, .github/CODEOWNERS, docs/CODEOWNERS, .gitlab/CODEOWNERS)")
    lines.append(summary_line(s) or "no decisions naming a path in history")
    if s.get("owners"):
        lines.append("owners of decided paths: " + ", ".join(f"{n} ({c})" for n, c in s["owners"]))
    if s.get("unowned"):
        lines.append("unowned paths carrying decisions:")
        for p, c in s["unowned"]:
            lines.append(f"  {p}  ({c} decision{'s' if c != 1 else ''})")
    return "\n".join(lines) + "\n"


def render_one(rel: str, o: dict[str, Any]) -> str:
    if o["owners"]:
        via = o["source"] + (f", rule {o['pattern']}" if o.get("pattern") else "")
        return f"{rel}: {' '.join(o['owners'])}  ({via})\n"
    where = f"{o['source']} has no rule for it" if o.get("source") else "no ownership file and no decisions.authorities"
    return f"{rel}: unowned  ({where})\n"
