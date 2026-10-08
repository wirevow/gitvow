"""`gitvow sessions export`: the session itself, complete and unredacted, as a bundle for a store the customer runs.

The record bundle (`export.py`) carries conclusions and refuses the conversation. This is the other artefact: the
transcript as the agent wrote it, the ledger entry, the session notes and the working-tree snapshots taken during
the session, for one repository, byte for byte. Nothing in it is redacted here, and the attestation says so,
because the store that receives it holds it complete under the customer's own retention and redacts on read
(decisions/2026-10-08-store-holds-everything.md). A store we host refuses this kind under every configuration; a
store the customer runs accepts it only when its operator has enabled it.

This is an explicit command by the person on the machine. `gitvow sync` never carries it, the committed consent
file cannot turn it on, and there is no outbox: an unreachable store is reported and the bundle stays where it was
written. Manifest `kind` is `sessions-raw`; the layout is flat so the store's part verb takes it unchanged:

  <session>.ledger.json          the ledger entry (`~/.gitvow/ledger/<session>.json`), or the open session's state
  <session>.transcript.jsonl     the agent's transcript file, verbatim, whatever agent wrote it
  <session>.notes.json           the session notes attached to this repository's commits
  <session>.snapshot-NNN.patch   each working-tree snapshot as a patch against the commit it was taken over
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import shutil
import time
from typing import Any

from . import __version__
from .decisions import identity
from .export import remote_name, since_date
from .snapshots import REF_PREFIX
from .state import git, load_state, toplevel

SCHEMA = 1
KIND = "sessions-raw"
CLASSES = ("ledger", "transcript", "notes", "snapshot")
PART_RE = re.compile(r"^[A-Za-z0-9._-]+\.(json|jsonl|patch)$")

NOT_REDACTED = (
    "none at export: complete and unredacted; redaction is the receiving store's step, on read, under its "
    "operator's rules"
)
ACCEPTED_ONLY_BY = (
    "a store the customer runs whose operator has enabled sessions-raw; a hosted store refuses this kind under "
    "every configuration"
)


def _safe(session_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "unknown")


def _same_repo(a: str | None, b: str) -> bool:
    if not a:
        return False
    try:
        return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(b)
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------------------------
# finding the sessions and their transcripts
# ---------------------------------------------------------------------------------------------------------------


def find_transcript(home: str, session_id: str, hint: str | None = None) -> str | None:
    """Where the agent left the transcript: the path the hooks recorded, else the places each agent keeps them.
    None when it is not on this machine; the bundle then says so instead of guessing."""
    if hint and os.path.isfile(hint):
        return hint
    sid = glob.escape(session_id)
    patterns = [
        os.path.join(home, ".claude", "projects", "*", f"{sid}.jsonl"),
        os.path.join(home, ".codex", "sessions", "**", f"*{sid}*.jsonl"),
        os.path.join(home, ".gemini", "tmp", "*", "chats", f"*{sid}*"),
        os.path.join(home, ".gemini", "tmp", "*", "chats", f"{sid}", "*"),
    ]
    for pat in patterns:
        hits = sorted(p for p in glob.glob(pat, recursive=True) if os.path.isfile(p))
        if hits:
            return hits[0]
    return None


def sessions_for(cwd: str, home: str, since: str | None = None, only: str | None = None) -> list[dict[str, Any]]:
    """The sessions that touched this repository: ledger entries whose repository is this one (or that reached
    it from another), plus the session still open here, which has no ledger entry yet."""
    top = toplevel(cwd) or cwd
    since_d = since_date(since)
    out: dict[str, dict[str, Any]] = {}
    led = os.path.join(home, ".gitvow", "ledger")
    for p in sorted(glob.glob(os.path.join(led, "*.json"))):
        try:
            with open(p) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        sid = rec.get("session_id")
        if not sid:
            continue
        here = _same_repo(rec.get("repo"), top) or any(_same_repo(r, top) for r in rec.get("repos_touched") or [])
        if not here:
            continue
        when = str(rec.get("ended") or rec.get("started") or "")[:10]
        if since_d and when and when < since_d:
            continue
        out[sid] = {"session_id": sid, "ledger": rec, "ledger_path": p, "open": False}
    st = load_state(cwd)
    sid = st.get("session_id")
    if sid and sid not in out:
        when = str(st.get("started") or "")[:10]
        if not (since_d and when and when < since_d):
            out[sid] = {
                "session_id": sid,
                "ledger": {
                    "session_id": sid,
                    "repo": top,
                    "started": st.get("started"),
                    "ended": None,
                    "intent": st.get("intent"),
                    "repos_touched": st.get("repos_touched") or [],
                    "open": True,
                },
                "ledger_path": None,
                "open": True,
            }
    rows = []
    for sid, s in out.items():
        if only and not (sid == only or sid.startswith(only)):
            continue
        hint = None
        if sid == st.get("session_id"):
            hint = st.get("transcript_path")
        hint = hint or s["ledger"].get("transcript_path")
        s["transcript_path"] = find_transcript(home, sid, hint)
        rows.append(s)
    rows.sort(key=lambda s: str(s["ledger"].get("started") or ""))
    return rows


# ---------------------------------------------------------------------------------------------------------------
# the parts
# ---------------------------------------------------------------------------------------------------------------


def _notes(top: str, sid: str) -> list[dict[str, Any]]:
    ref = f"gitvow/{_safe(sid)}"
    rc, out, _ = git(["notes", f"--ref={ref}", "list"], top)
    if rc != 0 or not out.strip():
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        commit = parts[1]
        rc, note, _ = git(["notes", f"--ref={ref}", "show", commit], top)
        if rc != 0:
            continue
        body: Any = note
        if note.startswith("gitvow-session"):
            try:
                body = json.loads(note.split("\n", 1)[1])
            except (ValueError, IndexError):
                body = note
        rows.append({"commit": commit, "note": body})
    rows.sort(key=lambda r: r["commit"])
    return rows


def _snapshots(top: str, sid: str) -> list[tuple[int, str, bytes]]:
    """(n, commit, patch) for every snapshot of the session: the diff against the commit it was taken over."""
    prefix = f"{REF_PREFIX}/{_safe(sid)}/"
    rc, out, _ = git(["for-each-ref", "--format=%(refname)%09%(objectname)", prefix], top)
    if rc != 0:
        return []
    rows = []
    for line in out.splitlines():
        ref, _, commit = line.partition("\t")
        try:
            n = int(ref.rsplit("/", 1)[1])
        except ValueError:
            continue
        rc, patch, _ = git(["diff", "--binary", f"{commit}^", commit], top)
        if rc != 0:
            rc, patch, _ = git(["show", "--format=", "--binary", commit], top)
        rows.append((n, commit, (patch or "").encode("utf-8", "surrogateescape")))
    rows.sort()
    return rows


def _canon(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def build(
    cwd: str,
    home: str | None = None,
    since: str | None = "90d",
    only: str | None = None,
    with_snapshots: bool = True,
) -> dict[str, Any]:
    """Assemble the bundle in memory: parts, manifest, attestation. Nothing is written, nothing is redacted."""
    top = toplevel(cwd)
    if not top:
        raise ValueError("not inside a git repository")
    home = home or os.path.expanduser("~")
    source = remote_name(top)
    found = sessions_for(top, home, since, only)
    if only and not found:
        raise ValueError(f"no session matching {only!r} touched this repository in the window")
    parts: dict[str, bytes] = {}
    manifest_parts: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []

    def add(name: str, cls: str, sid: str, data: bytes, **extra: Any) -> None:
        if not PART_RE.match(name):
            raise ValueError(f"part name {name!r} is not safe")
        parts[name] = data
        manifest_parts.append(
            {
                "path": name,
                "class": cls,
                "session_id": sid,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                **extra,
            }
        )

    for s in found:
        sid = s["session_id"]
        safe = _safe(sid)
        led = dict(s["ledger"])
        if s["transcript_path"]:
            led["transcript_path"] = s["transcript_path"]
        add(f"{safe}.ledger.json", "ledger", sid, _canon(led), open=s["open"])
        tbytes = 0
        if s["transcript_path"]:
            with open(s["transcript_path"], "rb") as fh:
                data = fh.read()
            tbytes = len(data)
            add(f"{safe}.transcript.jsonl", "transcript", sid, data, agent_file=os.path.basename(s["transcript_path"]))
        else:
            missing.append({"session_id": sid, "class": "transcript", "reason": "not found on this machine"})
        notes = _notes(top, sid)
        if notes:
            add(f"{safe}.notes.json", "notes", sid, _canon(notes), commits=len(notes))
        snaps = _snapshots(top, sid) if with_snapshots else []
        for n, commit, patch in snaps:
            add(f"{safe}.snapshot-{n:03d}.patch", "snapshot", sid, patch, n=n, over=commit)
        summary.append(
            {
                "session_id": sid,
                "open": s["open"],
                "started": led.get("started"),
                "ended": led.get("ended"),
                "agent_turns": led.get("assistant_turns"),
                "transcript": "included" if s["transcript_path"] else "missing",
                "transcript_bytes": tbytes,
                "notes": len(notes),
                "snapshots": len(snaps),
            }
        )
    _, head, _ = git(["rev-parse", "HEAD"], top)
    digest = hashlib.sha256("".join(p["sha256"] for p in manifest_parts).encode()).hexdigest()
    ident, _, name = identity(top)
    manifest = {
        "schema": SCHEMA,
        "kind": KIND,
        "gitvow": __version__,
        "source": {"remote": source},
        "frontier": {
            "head": head,
            "since": since_date(since),
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "sessions": summary,
        "parts": manifest_parts,
        "digest": f"sha256:{digest}",
        "idempotency_key": f"sha256:{digest}",
    }
    attestation = {
        "schema": SCHEMA,
        "kind": KIND,
        "redaction": NOT_REDACTED,
        "unredacted": True,
        "complete": not missing,
        "missing": missing,
        "exported_by": ident or name or "unknown",
        "command": "gitvow sessions export",
        "accepted_only_by": ACCEPTED_ONLY_BY,
        "never_by": ["gitvow sync", "a committed file", "a hook"],
        "signature": None,
    }
    return {"parts": parts, "manifest": manifest, "attestation": attestation, "top": top}


def write(bundle: dict[str, Any], out_dir: str) -> list[str]:
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, data in bundle["parts"].items():
        p = os.path.join(out_dir, name)
        with open(p, "wb") as fh:
            fh.write(data)
        written.append(p)
    for name in ("manifest", "attestation"):
        p = os.path.join(out_dir, f"{name}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(bundle[name], fh, indent=1, sort_keys=True)
        written.append(p)
    return written


def render_why(bundle: dict[str, Any]) -> str:
    m, a = bundle["manifest"], bundle["attestation"]
    total = sum(p["bytes"] for p in m["parts"])
    lines = [
        f"sessions of {m['source']['remote']} at {m['frontier']['head'][:12]}"
        + (f", since {m['frontier']['since']}" if m["frontier"]["since"] else ""),
        f"digest {m['digest'][:19]}…  {len(m['sessions'])} session(s), {len(m['parts'])} part(s), {total} bytes",
        "",
        "COMPLETE AND UNREDACTED. This is the conversation, not the record.",
        f"  accepted only by {a['accepted_only_by']}",
        "",
    ]
    if not m["sessions"]:
        lines.append("no session of this repository found in the window on this machine")
    for s in m["sessions"]:
        lines.append(
            f"  {s['session_id'][:12]}  {str(s['started'] or '')[:16]:16s}  transcript {s['transcript']}"
            + (f" ({s['transcript_bytes']} bytes)" if s["transcript_bytes"] else "")
            + f", notes on {s['notes']} commit(s), {s['snapshots']} snapshot(s)"
            + ("  [still open]" if s["open"] else "")
        )
    if a["missing"]:
        lines.append("")
        lines.append(
            f"missing on this machine: {len(a['missing'])} transcript(s); the attestation lists them and says the "
            "bundle is incomplete"
        )
    lines += ["", "redaction: " + a["redaction"], "never carried by: " + ", ".join(a["never_by"])]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------
# delivery: explicit, to one sink, no outbox
# ---------------------------------------------------------------------------------------------------------------


def deliver(sink: dict[str, Any], bundle_dir: str) -> dict[str, Any]:
    """Put the bundle into one sink. http: the store protocol, the store decides; dir: a copy under
    `<source>/sessions/<digest>/`; git: refused here, because a repository replicates to every clone and nothing
    scrubs it. Unreachable is reported, not queued."""
    from . import sync as sy

    with open(os.path.join(bundle_dir, "manifest.json")) as fh:
        m = json.load(fh)
    if m.get("kind") != KIND:
        raise ValueError(f"not a sessions bundle: kind {m.get('kind')!r}")
    source = m["source"]["remote"]
    digest = str(m["digest"]).split(":")[-1]
    base = {"source": source, "digest": digest, "sink": sink["name"]}
    if sink["type"] == "http":
        return sy._deliver_http(sink, bundle_dir, m)
    if sink["type"] == "git":
        return {
            **base,
            "status": "refused",
            "reason": "sessions never go into a git sink: every clone would replicate them and nothing scrubs them",
        }
    root = sink["path"]
    if not os.path.isdir(root):
        return {**base, "status": "unreachable", "reason": f"{root} is not a directory"}
    dest = os.path.join(root, sy._source_dir(source), "sessions", digest)
    if os.path.isdir(dest):
        return {**base, "status": "already held", "path": dest}
    shutil.copytree(bundle_dir, dest)
    return {**base, "status": "delivered", "path": dest}


def render_delivery(r: dict[str, Any]) -> str:
    line = f"  {r['sink']}: {r['status']} {r['source']} {r['digest'][:12]}"
    if r.get("reason"):
        line += f"  ({r['reason']})"
    if r.get("url"):
        line += f" → {r['url']}"
    if r.get("path"):
        line += f" → {r['path']}"
    return line + "\n"
