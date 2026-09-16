"""The collector: move the export bundle to a sink the repository has been pointed at, never blocking anything.

A sink is where bundles go. Two kinds ship here, both things the customer runs:

  git   a store repository (the layout the store protocol describes): the bundle is committed under
        `bundles/<source>/<digest>/` and the source's frontier file is appended; projections are the store's job
  dir   a directory or mounted object-store prefix: the bundle is copied to `<source>/<digest>/`

Sinks are configured in files that are never committed: `.gitvow/export.local.json` in the repository (which the
install adds to `.git/info/exclude`) or `~/.gitvow/sinks.json`. A committed file cannot name a destination, so a
fork can never inherit an upstream project's sink. When a sink is unreachable the bundle waits in
`~/.gitvow/outbox/<sink>/<source>/<digest>/` and the next sync drains it, oldest first. Idempotent by digest: a
bundle a sink already holds is reported, not resent.

The collector carries `kind: record` only. It refuses to move anything else.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any

from . import export as ex
from .state import toplevel

LOCAL_FILE = os.path.join(".gitvow", "export.local.json")
HOME_FILE = os.path.join(".gitvow", "sinks.json")
KINDS = ("git", "dir")


def _home(home: str | None) -> str:
    return home or os.path.expanduser("~")


def sinks(cwd: str, home: str | None = None) -> list[dict[str, Any]]:
    """Configured sinks, repository-local first, then the person's. Never from a committed file."""
    top = toplevel(cwd) or cwd
    out: list[dict[str, Any]] = []
    for p, scope in ((os.path.join(top, LOCAL_FILE), "repo"), (os.path.join(_home(home), HOME_FILE), "home")):
        if not os.path.exists(p):
            continue
        with open(p) as fh:
            data = json.load(fh)
        for s in data.get("sinks") or []:
            if not isinstance(s, dict) or s.get("type") not in KINDS or not s.get("path") or not s.get("name"):
                raise ValueError(f"{p}: each sink needs name, type ({'|'.join(KINDS)}) and path")
            out.append({**s, "path": os.path.expanduser(s["path"]), "scope": scope, "file": p})
    return out


def committed_sink_named(cwd: str) -> bool:
    """True when the committed consent file tries to name a sink, which is ignored with a warning: a committed
    destination is how a contributor's record ends up in an upstream project's store."""
    top = toplevel(cwd) or cwd
    p = os.path.join(top, ex.CONSENT_FILE)
    if not os.path.exists(p):
        return False
    with open(p) as fh:
        data = json.load(fh)
    return "sinks" in data or "sink" in data


def _source_dir(source: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9._-]+", "_", source)


def deliver(sink: dict[str, Any], bundle_dir: str) -> dict[str, Any]:
    """Put one bundle into one sink. Returns status: delivered, already held, or unreachable."""
    with open(os.path.join(bundle_dir, "manifest.json")) as fh:
        m = json.load(fh)
    if m.get("kind") != "record":
        raise ValueError(f"refusing to move a bundle of kind {m.get('kind')!r}; the collector carries 'record' only")
    source = m["source"]["remote"]
    digest = str(m["digest"]).split(":")[-1]
    root = sink["path"]
    if not os.path.isdir(root):
        return {"status": "unreachable", "source": source, "digest": digest, "sink": sink["name"]}
    if sink["type"] == "git":
        dest = os.path.join(root, "bundles", _source_dir(source), digest)
        if os.path.isdir(dest):
            return {"status": "already held", "source": source, "digest": digest, "sink": sink["name"]}
        shutil.copytree(bundle_dir, dest)
        front = os.path.join(root, "frontiers", f"{_source_dir(source)}.jsonl")
        os.makedirs(os.path.dirname(front), exist_ok=True)
        with open(front, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "digest": digest,
                        "head": m["frontier"]["head"],
                        "as_of": m["frontier"]["as_of"],
                        "since": m["frontier"].get("since"),
                        "gitvow": m.get("gitvow"),
                        "received": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "parts": [p["class"] for p in m.get("parts") or []],
                        "via": "gitvow sync",
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        if os.path.isdir(os.path.join(root, ".git")):
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "commit",
                    "-q",
                    "-m",
                    f"store: receive {source} {digest[:7]} (gitvow sync)",
                ],
                cwd=root,
                capture_output=True,
            )
        return {"status": "delivered", "source": source, "digest": digest, "sink": sink["name"], "path": dest}
    dest = os.path.join(root, _source_dir(source), digest)
    if os.path.isdir(dest):
        return {"status": "already held", "source": source, "digest": digest, "sink": sink["name"]}
    shutil.copytree(bundle_dir, dest)
    return {"status": "delivered", "source": source, "digest": digest, "sink": sink["name"], "path": dest}


def _outbox(home: str | None, sink_name: str) -> str:
    return os.path.join(_home(home), ".gitvow", "outbox", sink_name)


def queue(home: str | None, sink: dict[str, Any], bundle_dir: str, source: str, digest: str) -> str:
    dest = os.path.join(_outbox(home, sink["name"]), _source_dir(source), digest)
    if not os.path.isdir(dest):
        shutil.copytree(bundle_dir, dest)
    return dest


def drain(home: str | None, sink: dict[str, Any]) -> list[dict[str, Any]]:
    """Deliver everything waiting for this sink, oldest first; what is delivered leaves the outbox."""
    box = _outbox(home, sink["name"])
    if not os.path.isdir(box):
        return []
    waiting = []
    for sdir in sorted(os.listdir(box)):
        for digest in sorted(os.listdir(os.path.join(box, sdir))):
            p = os.path.join(box, sdir, digest)
            try:
                with open(os.path.join(p, "manifest.json")) as fh:
                    as_of = json.load(fh)["frontier"].get("as_of") or ""
            except (OSError, ValueError, KeyError):
                as_of = ""
            waiting.append((as_of, p))
    results = []
    for _, p in sorted(waiting):
        r = deliver(sink, p)
        if r["status"] in ("delivered", "already held"):
            shutil.rmtree(p, ignore_errors=True)
        results.append({**r, "from_outbox": True})
    return results


def sync(
    cwd: str,
    home: str | None = None,
    only: str | None = None,
    since: str | None = "90d",
    rules: list[tuple[str, str]] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Export the record and deliver it to every configured sink (or the one named), draining outboxes first."""
    targets = sinks(cwd, home)
    if only:
        targets = [s for s in targets if s["name"] == only]
        if not targets:
            raise ValueError(
                f"no sink named {only!r}; configured: {', '.join(s['name'] for s in sinks(cwd, home)) or 'none'}"
            )
    warnings = []
    if committed_sink_named(cwd):
        warnings.append(
            f"{ex.CONSENT_FILE} names a sink; ignored. A destination is never read from a committed file, so a fork "
            f"cannot inherit an upstream project's store. Put sinks in {LOCAL_FILE} or ~/{HOME_FILE}."
        )
    if not targets:
        return {
            "sinks": [],
            "results": [],
            "warnings": [*warnings, "no sinks configured; nothing moved"],
            "bundle": None,
        }
    b = ex.build(cwd, since=since, rules=rules)
    digest = b["manifest"]["digest"].split(":")[-1]
    source = b["manifest"]["source"]["remote"]
    if dry_run:
        return {
            "sinks": [s["name"] for s in targets],
            "results": [
                {"sink": s["name"], "status": "would deliver", "source": source, "digest": digest} for s in targets
            ],
            "warnings": warnings,
            "bundle": b["manifest"],
        }
    staging = os.path.join(_home(home), ".gitvow", "outbox", ".staging", digest)
    if os.path.isdir(staging):
        shutil.rmtree(staging)
    ex.write(b, staging)
    results = []
    try:
        for s in targets:
            results += drain(home, s)
            r = deliver(s, staging)
            if r["status"] == "unreachable":
                r["queued_at"] = queue(home, s, staging, source, digest)
            results.append(r)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"sinks": [s["name"] for s in targets], "results": results, "warnings": warnings, "bundle": b["manifest"]}


def render(out: dict[str, Any]) -> str:
    lines = []
    for w in out["warnings"]:
        lines.append(f"warning: {w}")
    if out["bundle"]:
        m = out["bundle"]
        lines.append(
            f"bundle {m['digest'][:19]}… for {m['source']['remote']} at {m['frontier']['head'][:12]}, {sum(p['rows'] for p in m['parts'])} rows in {len(m['parts'])} parts"
        )
    for r in out["results"]:
        extra = " (from outbox)" if r.get("from_outbox") else ""
        where = f" → {r['path']}" if r.get("path") else (f", waiting in {r['queued_at']}" if r.get("queued_at") else "")
        lines.append(f"  {r['sink']}: {r['status']}{extra} {r['source']} {r['digest'][:12]}{where}")
    return "\n".join(lines) + "\n"


def render_sinks(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No sinks configured. Add one to {LOCAL_FILE} (repository, never committed) or ~/{HOME_FILE}:\n"
            + json.dumps({"sinks": [{"name": "acme", "type": "git", "path": "~/acme-store"}]}, indent=1)
            + "\n"
        )
    return "\n".join(f"{s['name']}: {s['type']} {s['path']}  ({s['scope']}, {s['file']})" for s in rows) + "\n"
