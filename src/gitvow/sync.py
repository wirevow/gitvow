"""The collector: move the export bundle to a sink the repository has been pointed at, never blocking anything.

A sink is where bundles go. Three kinds ship here, all things the customer runs:

  git   a store repository (the layout the store protocol describes): the bundle is committed under
        `bundles/<source>/<digest>/` and the source's frontier file is appended; projections are the store's job
  dir   a directory or mounted object-store prefix: the bundle is copied to `<source>/<digest>/`
  http  a store served over HTTPS (the store protocol's verbs: begin, parts, commit); the bundle is uploaded part
        by part, idempotent by digest, and the pack and the brief come back from the same server

Sinks are configured in files that are never committed: `.gitvow/export.local.json` in the repository (which the
install adds to `.git/info/exclude`) or `~/.gitvow/sinks.json`. A committed file cannot name a destination, so a
fork can never inherit an upstream project's sink. When a sink is unreachable the bundle waits in
`~/.gitvow/outbox/<sink>/<source>/<digest>/` and the next sync drains it, oldest first. A sink that refuses a
bundle (a 4xx from an http store) is reported with the reason and the bundle is not queued: retrying a refusal
changes nothing. Idempotent by digest: a bundle a sink already holds is reported, not resent.

The collector carries `kind: record` only. It refuses to move anything else.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import export as ex
from .state import toplevel

LOCAL_FILE = os.path.join(".gitvow", "export.local.json")
HOME_FILE = os.path.join(".gitvow", "sinks.json")
KINDS = ("git", "dir", "http")
HTTP_TIMEOUT = 10


class UnreachableError(Exception):
    pass


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
            if not isinstance(s, dict) or s.get("type") not in KINDS or not s.get("name"):
                raise ValueError(f"{p}: each sink needs name, type ({'|'.join(KINDS)}) and path or url")
            if s["type"] == "http":
                if not str(s.get("url", "")).startswith(("http://", "https://")):
                    raise ValueError(
                        f"{p}: sink {s['name']}: an http sink needs a url starting with http:// or https://"
                    )
                out.append({**s, "url": s["url"].rstrip("/"), "path": None, "scope": scope, "file": p})
            else:
                if not s.get("path"):
                    raise ValueError(f"{p}: sink {s['name']}: a {s['type']} sink needs a path")
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
    from .pack import source_dir

    return source_dir(source)


# ---------------------------------------------------------------------------------------------------------------
# http
# ---------------------------------------------------------------------------------------------------------------


def _token(sink: dict[str, Any]) -> str | None:
    """The bearer token: from the environment variable `token_env` names (preferred), or `token` in the local
    sink file. Never from a committed file, like everything else here."""
    if sink.get("token_env"):
        return os.environ.get(sink["token_env"]) or None
    return sink.get("token") or None


def _http(
    sink: dict[str, Any],
    method: str,
    path: str,
    body: bytes | None = None,
    ctype: str = "application/json",
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """One request to an http sink. Returns (status, parsed JSON or raw bytes). Raises UnreachableError when the
    server cannot be reached or answers 5xx; a 4xx is an answer and comes back as a status."""
    if not sink["url"].startswith(("http://", "https://")):
        raise UnreachableError("sink url must be http:// or https://")
    req = urllib.request.Request(sink["url"] + path, data=body, method=method)  # noqa: S310 - scheme checked above
    if body is not None:
        req.add_header("Content-Type", ctype)
    tok = _token(sink)
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:  # noqa: S310 # nosec B310 - scheme checked above
            code, data = r.status, r.read()
    except urllib.error.HTTPError as e:
        code, data = e.code, e.read()
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise UnreachableError(str(getattr(e, "reason", e))) from e
    if code >= 500:
        raise UnreachableError(f"HTTP {code}")
    try:
        return code, json.loads(data)
    except ValueError:
        return code, data


def _deliver_http(sink: dict[str, Any], bundle_dir: str, m: dict[str, Any]) -> dict[str, Any]:
    source = m["source"]["remote"]
    digest = str(m["digest"]).split(":")[-1]
    base = {"source": source, "digest": digest, "sink": sink["name"]}
    with open(os.path.join(bundle_dir, "manifest.json"), "rb") as fh:
        manifest = fh.read()
    try:
        code, r = _http(sink, "POST", "/v1/bundles", manifest, headers={"Idempotency-Key": f"sha256:{digest}"})
        if code >= 400:
            return {**base, "status": "refused", "reason": _reason(r)}
        if r.get("status") == "already held":
            return {**base, "status": "already held"}
        held = set(r.get("held") or [])
        for part in m.get("parts") or []:
            if part["path"] in held:
                continue
            with open(os.path.join(bundle_dir, part["path"]), "rb") as fh:
                data = fh.read()
            code, r = _http(
                sink,
                "PUT",
                f"/v1/bundles/{digest}/parts/{urllib.parse.quote(part['path'])}",
                data,
                ctype="application/octet-stream",
            )
            if code >= 400:
                return {**base, "status": "refused", "reason": _reason(r)}
        with open(os.path.join(bundle_dir, "attestation.json"), "rb") as fh:
            attestation = fh.read()
        code, r = _http(sink, "POST", f"/v1/bundles/{digest}/commit", attestation)
        if code >= 400:
            return {**base, "status": "refused", "reason": _reason(r)}
        status = "delivered" if r.get("status") == "ingested" else r.get("status", "delivered")
        return {**base, "status": status, "commit": r.get("commit"), "url": f"{sink['url']}/v1/bundles/{digest}"}
    except UnreachableError as e:
        return {**base, "status": "unreachable", "reason": str(e)}


def _reason(r: Any) -> str:
    if isinstance(r, dict) and r.get("error"):
        return str(r["error"])
    return "refused"


# ---------------------------------------------------------------------------------------------------------------
# deliver
# ---------------------------------------------------------------------------------------------------------------


def deliver(sink: dict[str, Any], bundle_dir: str) -> dict[str, Any]:
    """Put one bundle into one sink. Returns status: delivered, already held, refused, or unreachable."""
    with open(os.path.join(bundle_dir, "manifest.json")) as fh:
        m = json.load(fh)
    if m.get("kind") != "record":
        raise ValueError(f"refusing to move a bundle of kind {m.get('kind')!r}; the collector carries 'record' only")
    if sink["type"] == "http":
        return _deliver_http(sink, bundle_dir, m)
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
        committed: bool | None = None
        if os.path.isdir(os.path.join(root, ".git")):
            subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
            # The store repository may have no committer identity of its own (a fresh clone on a CI runner, a
            # machine with no global git config); git on Linux refuses to commit without one, and a bundle written
            # but not committed is an ingest log with a hole. Fall back to a store identity for this commit only.
            ident = subprocess.run(["git", "config", "user.email"], cwd=root, capture_output=True, text=True)
            extra = (
                [] if ident.stdout.strip() else ["-c", "user.name=gitvow store", "-c", "user.email=store@gitvow.local"]
            )
            r = subprocess.run(
                [
                    "git",
                    "-c",
                    "core.hooksPath=/dev/null",
                    *extra,
                    "commit",
                    "-q",
                    "-m",
                    f"store: receive {source} {digest[:7]} (gitvow sync)",
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            committed = r.returncode == 0
        out = {"status": "delivered", "source": source, "digest": digest, "sink": sink["name"], "path": dest}
        if committed is not None:
            out["committed"] = committed
        return out
    dest = os.path.join(root, _source_dir(source), digest)
    if os.path.isdir(dest):
        return {"status": "already held", "source": source, "digest": digest, "sink": sink["name"]}
    shutil.copytree(bundle_dir, dest)
    return {"status": "delivered", "source": source, "digest": digest, "sink": sink["name"], "path": dest}


# ---------------------------------------------------------------------------------------------------------------
# what comes back
# ---------------------------------------------------------------------------------------------------------------


def _published(sink: dict[str, Any], kind: str, source: str) -> bytes | None:
    """The bytes the store publishes for this repository: the pack or the brief. None when there is none."""
    if sink["type"] == "git":
        sub = "packs" if kind == "pack" else "brief"
        p = os.path.join(sink["path"], sub, f"{_source_dir(source)}.json")
        if not os.path.exists(p):
            return None
        with open(p, "rb") as fh:
            return fh.read()
    verb = "/v1/rules" if kind == "pack" else "/v1/brief"
    code, r = _http(sink, "GET", f"{verb}?repo={urllib.parse.quote(source, safe='')}")
    if code == 404:
        return None
    if code >= 400 or not isinstance(r, dict):
        raise UnreachableError(_reason(r) if code >= 400 else "not JSON")
    r.pop("schema", None)  # the envelope, not the document
    return json.dumps(r, sort_keys=True).encode("utf-8")


def fetch(sink: dict[str, Any], source: str, home: str | None) -> dict[str, str]:
    """Pull what the store publishes for this repository, the pack and the brief, into `~/.gitvow/cache/`. Git
    and http sinks only: a `dir` sink is a drop, not a store. The cache is advisory; a file that is not JSON is
    left out and reported; an unreachable store leaves the last good copy in place."""
    from .pack import brief_path, meta_path, pack_path

    if sink["type"] == "dir":
        return {}
    out: dict[str, str] = {}
    for kind, dest in (("pack", pack_path(home, source)), ("brief", brief_path(home, source))):
        try:
            data = _published(sink, kind, source)
        except UnreachableError:
            out[kind] = "unreachable"
            continue
        if data is None:
            out[kind] = "none"
            continue
        try:
            json.loads(data)
        except ValueError:
            out[kind] = "invalid"
            continue
        if os.path.exists(dest):
            with open(dest, "rb") as fh:
                if fh.read() == data:
                    out[kind] = "unchanged"
                    continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(data)
        with open(meta_path(dest), "w", encoding="utf-8") as fh:
            json.dump({"sink": sink["name"], "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, fh)
        out[kind] = "fetched"
    return out


# ---------------------------------------------------------------------------------------------------------------
# outbox and sync
# ---------------------------------------------------------------------------------------------------------------


def _outbox(home: str | None, sink_name: str) -> str:
    return os.path.join(_home(home), ".gitvow", "outbox", sink_name)


def queue(home: str | None, sink: dict[str, Any], bundle_dir: str, source: str, digest: str) -> str:
    dest = os.path.join(_outbox(home, sink["name"]), _source_dir(source), digest)
    if not os.path.isdir(dest):
        shutil.copytree(bundle_dir, dest)
    return dest


def drain(home: str | None, sink: dict[str, Any]) -> list[dict[str, Any]]:
    """Deliver everything waiting for this sink, oldest first; what is delivered or refused leaves the outbox."""
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
        if r["status"] in ("delivered", "already held", "refused"):
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
            elif r["status"] != "refused":
                r["fetched"] = fetch(s, source, home)
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
            f"bundle {m['digest'][:19]}… for {m['source']['remote']} at {m['frontier']['head'][:12]}, "
            f"{sum(p['rows'] for p in m['parts'])} rows in {len(m['parts'])} parts"
        )
    for r in out["results"]:
        extra = " (from outbox)" if r.get("from_outbox") else ""
        where = ""
        if r.get("path") or r.get("url"):
            where = f" → {r.get('path') or r.get('url')}"
        elif r.get("queued_at"):
            where = f", waiting in {r['queued_at']}"
        if r.get("reason"):
            where += f" ({r['reason']})"
        lines.append(f"  {r['sink']}: {r['status']}{extra} {r['source']} {r['digest'][:12]}{where}")
        got = {k: v for k, v in (r.get("fetched") or {}).items() if v != "none"}
        if got:
            lines.append("    from the store: " + ", ".join(f"{k} {v}" for k, v in got.items()))
    return "\n".join(lines) + "\n"


def render_sinks(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            f"No sinks configured. Add one to {LOCAL_FILE} (repository, never committed) or ~/{HOME_FILE}:\n"
            + json.dumps(
                {
                    "sinks": [
                        {"name": "acme", "type": "git", "path": "~/acme-store"},
                        {
                            "name": "acme-store",
                            "type": "http",
                            "url": "https://store.acme.internal",
                            "token_env": "WIREVOW_STORE_TOKEN",  # nosec B105 - the name of a variable, not a secret
                        },
                    ]
                },
                indent=1,
            )
            + "\n"
        )
    return (
        "\n".join(f"{s['name']}: {s['type']} {s.get('url') or s['path']}  ({s['scope']}, {s['file']})" for s in rows)
        + "\n"
    )
