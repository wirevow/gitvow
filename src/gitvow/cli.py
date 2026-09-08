"""gitvow command line."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .collect import collect, summarize_text
from .hooks import HANDLERS
from .install import install_repo, install_user, uninstall_repo, uninstall_user
from .policy import PolicyError, evaluate, load_policy
from .state import git


def cmd_hook(a: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    handler = HANDLERS.get(a.event)
    if not handler:
        print(f"unknown hook event {a.event}", file=sys.stderr)
        return 1
    code, msg = handler(payload)
    if msg:
        print(msg, file=sys.stderr)
    return code


def cmd_install(a: argparse.Namespace) -> int:
    done = install_user(os.path.expanduser("~")) if a.user else install_repo(os.path.abspath(a.repo))
    print("\n".join(done))
    return 0


def cmd_uninstall(a: argparse.Namespace) -> int:
    done = (
        uninstall_user(os.path.expanduser("~"), a.purge_policy, a.purge_ledger)
        if a.user
        else uninstall_repo(os.path.abspath(a.repo), a.purge_notes)
    )
    print("\n".join(done))
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    """Dry-run the policy against a command, path or MCP tool name."""
    try:
        pol = load_policy(os.getcwd())
    except PolicyError as e:
        print(f"policy error: {e}", file=sys.stderr)
        return 2
    if a.path:
        d = evaluate(pol, "Edit", {"file_path": a.path})
    elif a.mcp:
        d = evaluate(pol, a.mcp, {})
    else:
        d = evaluate(pol, "Bash", {"command": " ".join(a.command)})
    print(d.outcome.upper() + (f": {d.reason}" if d.reason else ""))
    return 2 if d.blocks else 0


def cmd_show(a: argparse.Namespace) -> int:
    """Print a commit's trailers and session note."""
    rc, msg, _ = git(["log", "-1", "--format=%H%n%s%n%b", a.commit], os.getcwd())
    if rc != 0:
        print(f"no such commit: {a.commit}", file=sys.stderr)
        return 1
    print(msg)
    rc, note, _ = git(["notes", "--ref=sessions", "show", a.commit], os.getcwd())
    print(note if rc == 0 else "(no session note)")
    return 0


def cmd_collect(a: argparse.Namespace) -> int:
    w = collect(os.path.expanduser("~"), a.out)
    print(summarize_text(w))
    print(f"collected into {w}  (redacted at write time; review before sending)")
    return 0


def cmd_summarize(a: argparse.Namespace) -> int:
    print(summarize_text(a.dir))
    return 0


def cmd_selftest(a: argparse.Namespace) -> int:
    from .selftest import run

    return run()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gitvow", description="Provenance and policy gate for agent coding sessions.")
    p.add_argument("--version", action="version", version=f"gitvow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("hook", help="run as a Claude Code hook (reads JSON on stdin)")
    s.add_argument("event")
    s.set_defaults(f=cmd_hook)
    s = sub.add_parser("install", help="install per user (--user) or into a repo")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--user", action="store_true")
    s.set_defaults(f=cmd_install)
    s = sub.add_parser("uninstall", help="remove what install added")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--user", action="store_true")
    s.add_argument("--purge-notes", action="store_true")
    s.add_argument("--purge-policy", action="store_true")
    s.add_argument("--purge-ledger", action="store_true")
    s.set_defaults(f=cmd_uninstall)
    s = sub.add_parser("check", help="dry-run the policy: gitvow check -- git push --force")
    s.add_argument("command", nargs="*")
    s.add_argument("--path")
    s.add_argument("--mcp")
    s.set_defaults(f=cmd_check)
    s = sub.add_parser("show", help="print a commit's trailers and session note")
    s.add_argument("commit", nargs="?", default="HEAD")
    s.set_defaults(f=cmd_show)
    s = sub.add_parser("collect", help="gather ledger, logs, trailers and notes into one directory")
    s.add_argument("--out", default=os.path.expanduser("~/Desktop"))
    s.set_defaults(f=cmd_collect)
    s = sub.add_parser("summarize", help="metrics from a collected directory")
    s.add_argument("dir")
    s.set_defaults(f=cmd_summarize)
    s = sub.add_parser("selftest", help="prove the hooks work here without touching a real repo")
    s.set_defaults(f=cmd_selftest)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
