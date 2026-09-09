"""gitvow command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import __version__
from . import decisions as dec
from . import digest as dg
from . import recall as rec
from . import snapshots as snap
from .collect import collect, summarize_text
from .hooks import HANDLERS, LEGACY_NOTES_REF, notes_ref
from .install import install_repo, install_user, uninstall_repo, uninstall_user
from .policy import PolicyError, evaluate, load_policy
from .providers import QUESTIONS, ask
from .redact import RedactionError, load_rules, redact
from .report import build, render_markdown
from .state import git


def cmd_hook(a: argparse.Namespace) -> int:
    from .adapters import normalize, respond

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        payload = {}
    from .adapters import AdapterError, external_normalize, external_path, external_respond

    agent = a.agent or "claude"
    exe = external_path(agent)
    try:
        calls = external_normalize(exe, a.event, payload) if exe else normalize(agent, a.event, payload)
    except AdapterError as e:
        # a broken external adapter must not let a tool call through unseen
        print(f"BLOCKED: agent adapter failed ({e}).", file=sys.stderr)
        return 2
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not calls:
        return 0  # an event this adapter does not use
    worst, messages = 0, []
    for gv_event, p in calls:
        handler = HANDLERS.get(gv_event)
        if not handler:
            print(f"unknown hook event {gv_event}", file=sys.stderr)
            return 1
        code, msg = handler(p)
        if code == 2 and len(calls) > 1 and p.get("tool_input", {}).get("file_path"):
            msg = f"{msg} [file: {p['tool_input']['file_path']}]"
        worst = max(worst, code)
        if msg:
            messages.append(msg)
    if a.event == "SessionStart" and worst == 0 and messages and not exe and agent not in ("cursor", "copilot"):
        print("\n".join(messages))  # SessionStart stdout becomes context for the agent
        return 0
    try:
        exit_code, out, err = (
            external_respond(exe, worst, "\n".join(messages)) if exe else respond(agent, worst, "\n".join(messages))
        )
    except AdapterError as e:
        print(f"BLOCKED: agent adapter failed ({e}).", file=sys.stderr)
        return 2
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return exit_code


def _check_agent(agent: str) -> int:
    from .adapters import AGENTS, external_path

    if agent in AGENTS or external_path(agent):
        return 0
    print(
        f"unknown agent {agent!r}: not built in and no gitvow-agent-{agent} executable on PATH or in ~/.gitvow/agents",
        file=sys.stderr,
    )
    return 1


def cmd_install(a: argparse.Namespace) -> int:
    agent = a.agent or "claude"
    if _check_agent(agent):
        return 1
    if a.user:
        done = install_user(os.path.expanduser("~"), agent=agent)
    else:
        done = install_repo(os.path.abspath(a.repo), agent=agent)
    print("\n".join(done))
    return 0


def cmd_uninstall(a: argparse.Namespace) -> int:
    agent = a.agent or "claude"
    if _check_agent(agent):
        return 1
    done = (
        uninstall_user(os.path.expanduser("~"), a.purge_policy, a.purge_ledger, agent=agent)
        if a.user
        else uninstall_repo(os.path.abspath(a.repo), a.purge_notes, a.purge_snapshots, agent=agent)
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
    label = "CONFIRM AT COMMIT" if d.deferred else d.outcome.upper()
    print(label + (f": {d.reason}" if d.reason else ""))
    return 2 if d.blocks else 0


def cmd_decisions(a: argparse.Namespace) -> int:
    """The card: open findings in this repository."""
    cwd = os.getcwd()
    if a.json:
        print(json.dumps(dec.open_findings(cwd), indent=1))
        return 0
    try:
        pol = load_policy(cwd)
    except PolicyError:
        pol = None
    print(dec.card(cwd, for_agent=False, pol=pol), end="")
    return 0


def cmd_rules(a: argparse.Namespace) -> int:
    """Earned rules from the decision history; --write puts them into an agent instruction file."""
    from .rules import INSTRUCTION_FILES, derive, render, write_section

    cwd = os.getcwd()
    try:
        pol = load_policy(cwd)
    except PolicyError as e:
        print(f"policy error: {e}", file=sys.stderr)
        return 2
    d = derive(cwd, pol)
    if a.json:
        print(json.dumps(d, indent=1))
        return 0
    if a.write:
        from .state import toplevel

        top = toplevel(cwd) or cwd
        target = a.file or INSTRUCTION_FILES.get(a.agent, "CLAUDE.md")
        print(write_section(os.path.join(top, target), render(d)))
        return 0
    text = render(d, for_agent=False)
    if not text:
        print(
            f"no earned rules yet: a finding becomes one after {d['threshold']} consistent answers by authorities "
            f"(decisions.rule_threshold), confirmed within {d['decay_days']} days."
        )
        if d["candidates"]:
            for r in d["candidates"]:
                print(f"  {r['finding']}: {r['answer']} {r['count']} times (last {r['last']} by {', '.join(r['by'])})")
        return 0
    print(text, end="")
    return 0


def cmd_decide(a: argparse.Namespace) -> int:
    """Record a person's answer to one finding or all of them."""
    cwd = os.getcwd()
    try:
        pol = load_policy(cwd)
    except PolicyError as e:
        print(f"policy error: {e}", file=sys.stderr)
        return 2
    try:
        rules = load_rules(cwd, os.path.expanduser("~"))
    except RedactionError as e:
        print(f"redaction rules invalid: {e}", file=sys.stderr)
        return 2
    from .state import load_state
    from .transcript import summarize

    st = load_state(cwd)
    turns = summarize(st.get("transcript_path"), rules=rules)["user_turns"] if st.get("transcript_path") else None
    try:
        done = dec.decide(cwd, a.finding, a.answer, pol, a.scope, a.reason, a.by, rules, turns)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    for f in done:
        print(dec.trailer_line(f))
    if done and done[0]["decision"]["authority"] == "none":
        print("recorded; the committer is not named under decisions.authorities", file=sys.stderr)
    print("written as trailers on the next commit", file=sys.stderr)
    return 0


def cmd_ask(a: argparse.Namespace) -> int:
    """Ask every configured provider one question and show the gate's decision."""
    try:
        pol = load_policy(os.getcwd())
    except PolicyError as e:
        print(f"policy error: {e}", file=sys.stderr)
        return 2
    if not pol.get("providers"):
        print("no providers configured in the active policy", file=sys.stderr)
        return 1
    path = a.path or (a.subject if a.question == "gate_bearing" else "")
    answers = ask(pol, "Edit", {"file_path": path}, os.getcwd(), only=[(a.question, a.subject)])
    for ans in answers:
        ev = " — " + "; ".join(ans.evidence) if ans.evidence else ""
        print(f"{ans.provider}: {ans.answer}{ev}")
    failed = any(x.answer == "failed" for x in answers)
    yes = any(x.answer == "yes" for x in answers)
    print("decision: " + ("CONFIRM" if failed else "CONFIRM AT COMMIT" if yes else "ALLOW"))
    return 2 if failed else 0


def cmd_show(a: argparse.Namespace) -> int:
    """Print a commit's trailers and session note."""
    rc, msg, _ = git(["log", "-1", "--format=%H%n%s%n%b", a.commit], os.getcwd())
    if rc != 0:
        print(f"no such commit: {a.commit}", file=sys.stderr)
        return 1
    print(msg)
    m = re.search(r"^Gitvow-Session:\s*(\S+)", msg, re.M)
    refs = ([notes_ref(m.group(1))] if m else []) + [LEGACY_NOTES_REF]
    for ref in refs:
        rc, note, _ = git(["notes", f"--ref={ref}", "show", a.commit], os.getcwd())
        if rc == 0:
            print(note)
            return 0
    print("(no session note)")
    return 0


def cmd_redact(a: argparse.Namespace) -> int:
    """Apply the built-in layers plus the rules files to text."""
    try:
        rules = load_rules(os.getcwd(), os.path.expanduser("~"))
    except RedactionError as e:
        print(f"redaction rules error: {e}", file=sys.stderr)
        return 2
    print(redact(" ".join(a.text), rules))
    return 0


def cmd_report(a: argparse.Namespace) -> int:
    """Per-commit report for base..head; exit 1 when --require-notes and a trailered commit has no note."""
    try:
        r = build(os.getcwd(), a.base, a.head, a.target)
    except ValueError as e:
        print(f"report error: {e}", file=sys.stderr)
        return 1
    if a.decisions_summary:
        from .report import decisions_summary

        print(decisions_summary(r), end="")
        return 0
    print(json.dumps(r, indent=1) if a.json else render_markdown(r), end="" if not a.json else "\n")
    if a.require_notes and r["missing_notes"]:
        print(f"missing session notes for: {', '.join(r['missing_notes'])}", file=sys.stderr)
        return 1
    return 0


def cmd_snapshots(a: argparse.Namespace) -> int:
    cwd = os.getcwd()
    if a.sub == "prune":
        days = None
        if a.older_than:
            m = re.fullmatch(r"(\d+)([dh]?)", a.older_than)
            if not m:
                print("--older-than takes a number of days, e.g. 14d", file=sys.stderr)
                return 1
            days = int(m.group(1)) / (24 if m.group(2) == "h" else 1)
        print(f"pruned {snap.prune(cwd, days, a.session)} snapshot(s)")
        return 0
    rows = snap.list_snapshots(cwd, a.session)
    if not rows:
        print("no snapshots" + (f" for session {a.session}" if a.session else ""))
        return 0
    cur = None
    for r in rows:
        if r["session"] != cur:
            cur = r["session"]
            print(f"session {cur[:8]}   {sum(1 for x in rows if x['session'] == cur)} snapshots")
            print("  n    taken                tool        file                                  changed vs HEAD")
        print(
            f"  {r['n']:<4d} {r['taken']:<20s} {r['tool']:<11s} {r['file'][:37]:<37s} {r['changed_files']} file{'s' if r['changed_files'] != 1 else ''}"
        )
    return 0


def _resolve_or_fail(a: argparse.Namespace) -> str | None:
    ref = snap.resolve(os.getcwd(), a.session, a.n)
    if not ref:
        print(f"no snapshot {a.n} for a unique session matching {a.session!r}", file=sys.stderr)
    return ref


def cmd_diff(a: argparse.Namespace) -> int:
    ref = _resolve_or_fail(a)
    if not ref:
        return 1
    print(snap.diff(os.getcwd(), ref, a.full))
    return 0


def cmd_restore(a: argparse.Namespace) -> int:
    ref = _resolve_or_fail(a)
    if not ref:
        return 1
    rc, msg = snap.restore(os.getcwd(), ref, a.to)
    print(
        f"restored {ref} into {msg} (detached worktree; remove with: git worktree remove {msg})" if rc == 0 else msg,
        file=sys.stderr if rc else sys.stdout,
    )
    return rc


def cmd_why(a: argparse.Namespace) -> int:
    print(rec.why(os.getcwd(), a.path))
    return 0


def cmd_trace(a: argparse.Namespace) -> int:
    print(rec.trace(os.getcwd(), a.spec))
    return 0


def cmd_recall(a: argparse.Namespace) -> int:
    print(rec.recall(os.getcwd(), a.words, None, a.limit))
    return 0


def cmd_handoff(a: argparse.Namespace) -> int:
    print(rec.handoff(os.getcwd(), a.session))
    return 0


def cmd_digest(a: argparse.Namespace) -> int:
    d = dg.build(os.getcwd(), a.since)
    print(json.dumps(d, indent=1) if a.json else dg.render(d), end="\n" if a.json else "")
    return 0


def cmd_push_notes(a: argparse.Namespace) -> int:
    import subprocess

    env = {**os.environ, "GITVOW_PUSHING_NOTES": "1"}  # the pre-push hook must not push the same refs again
    r = subprocess.run(
        ["git", "push", a.remote, "refs/notes/gitvow/*:refs/notes/gitvow/*"], env=env, capture_output=True, text=True
    )
    print((r.stderr or r.stdout).strip() or "notes pushed", file=sys.stderr if r.returncode else sys.stdout)
    return 1 if r.returncode else 0


def cmd_collect(a: argparse.Namespace) -> int:
    w = collect(os.path.expanduser("~"), a.out)
    print(summarize_text(w))
    print(f"collected into {w}  (redacted at write time; review before sending)")
    return 0


def cmd_summarize(a: argparse.Namespace) -> int:
    print(summarize_text(a.dir))
    return 0


def cmd_selftest(a: argparse.Namespace) -> int:
    from .selftest import run, run_agent

    if a.agent and a.agent != "claude":
        if _check_agent(a.agent):
            return 1
        return run_agent(a.agent)
    return run()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gitvow", description="Provenance and policy gate for agent coding sessions.")
    p.add_argument("--version", action="version", version=f"gitvow {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("hook", help="run as an agent hook (reads the agent's JSON on stdin)")
    s.add_argument(
        "--agent",
        default="claude",
        help="claude|codex|gemini|cursor|copilot|factory or an external gitvow-agent-<name>",
    )
    s.add_argument("event")
    s.set_defaults(f=cmd_hook)
    s = sub.add_parser("install", help="install per user (--user) or into a repo")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--user", action="store_true")
    s.add_argument(
        "--agent",
        default="claude",
        help="claude|codex|gemini|cursor|copilot|factory or an external gitvow-agent-<name>",
    )
    s.set_defaults(f=cmd_install)
    s = sub.add_parser("uninstall", help="remove what install added")
    s.add_argument("repo", nargs="?", default=".")
    s.add_argument("--user", action="store_true")
    s.add_argument("--purge-notes", action="store_true")
    s.add_argument("--purge-policy", action="store_true")
    s.add_argument("--purge-ledger", action="store_true")
    s.add_argument("--purge-snapshots", action="store_true")
    s.add_argument(
        "--agent",
        default="claude",
        help="claude|codex|gemini|cursor|copilot|factory or an external gitvow-agent-<name>",
    )
    s.set_defaults(f=cmd_uninstall)
    s = sub.add_parser("check", help="dry-run the policy: gitvow check -- git push --force")
    s.add_argument("command", nargs="*")
    s.add_argument("--path")
    s.add_argument("--mcp")
    s.set_defaults(f=cmd_check)
    s = sub.add_parser("show", help="print a commit's trailers and session note")
    s.add_argument("commit", nargs="?", default="HEAD")
    s.set_defaults(f=cmd_show)
    s = sub.add_parser("ask", help="ask configured providers a question: gitvow ask route_gate /v1/x --path src/api.py")
    s.add_argument("question", choices=QUESTIONS)
    s.add_argument("subject")
    s.add_argument("--path", default="")
    s.set_defaults(f=cmd_ask)
    s = sub.add_parser("redact", help="apply built-in redaction plus your rules files to text")
    s.add_argument("text", nargs="+")
    s.set_defaults(f=cmd_redact)
    s = sub.add_parser("report", help="per-commit report for a range: trailers, notes, attribution, said vs did")
    s.add_argument("--base", required=True)
    s.add_argument("--head", default="HEAD")
    s.add_argument("--json", action="store_true")
    s.add_argument("--require-notes", action="store_true")
    s.add_argument("--target", help="branch the change is going to; scoped decisions not covering it are reopened")
    s.add_argument("--decisions-summary", action="store_true", help="print only the trailer block for a PR description")
    s.set_defaults(f=cmd_report)
    s = sub.add_parser("decisions", help="the card: open findings in this repository with evidence and the record")
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_decisions)
    s = sub.add_parser(
        "rules", help="earned rules from the decision history; --write updates the agent instruction file"
    )
    s.add_argument("--json", action="store_true")
    s.add_argument("--write", action="store_true", help="write the managed section into the instruction file")
    s.add_argument(
        "--agent",
        default="claude",
        help="which agent's instruction file (claude, codex, gemini, cursor, copilot, factory)",
    )
    s.add_argument("--file", help="instruction file path, overriding --agent")
    s.set_defaults(f=cmd_rules)
    s = sub.add_parser("decide", help="record a person's answer: gitvow decide 1 accept --scope staging")
    s.add_argument("finding", help="finding number from `gitvow decisions`, or 'all'")
    s.add_argument("answer", choices=["accept", "decline"])
    s.add_argument("--scope", help="environment or branch the answer is limited to")
    s.add_argument("--reason", help="one phrase, optional")
    s.add_argument("--by", help="who decided, when not the committer")
    s.set_defaults(f=cmd_decide)
    s = sub.add_parser(
        "snapshots", help="list working-tree snapshots taken after agent edits; 'snapshots prune' deletes"
    )
    s.add_argument("sub", nargs="?", choices=["prune"])
    s.add_argument("--session")
    s.add_argument("--all", action="store_true")
    s.add_argument("--older-than")
    s.set_defaults(f=cmd_snapshots)
    s = sub.add_parser("diff", help="what the agent had changed at snapshot n: gitvow diff <session> <n>")
    s.add_argument("session")
    s.add_argument("n", type=int)
    s.add_argument("--full", action="store_true")
    s.set_defaults(f=cmd_diff)
    s = sub.add_parser("restore", help="check a snapshot out into a detached scratch worktree")
    s.add_argument("session")
    s.add_argument("n", type=int)
    s.add_argument("--to")
    s.set_defaults(f=cmd_restore)
    s = sub.add_parser("why", help="which sessions shaped a file: commits, plans, attribution")
    s.add_argument("path")
    s.set_defaults(f=cmd_why)
    s = sub.add_parser("trace", help="who wrote these lines: gitvow trace path[:start-end]")
    s.add_argument("spec")
    s.set_defaults(f=cmd_trace)
    s = sub.add_parser("recall", help="sessions whose notes or ledger mention the words")
    s.add_argument("words", nargs="+")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(f=cmd_recall)
    s = sub.add_parser("handoff", help="markdown summary for the next agent")
    s.add_argument("--session")
    s.set_defaults(f=cmd_handoff)
    s = sub.add_parser("digest", help="period summary: agent vs human commits, sessions, attribution, gate activity")
    s.add_argument("--since", default="7d")
    s.add_argument("--json", action="store_true")
    s.set_defaults(f=cmd_digest)
    s = sub.add_parser("push-notes", help="push refs/notes/gitvow/* to a remote (default origin)")
    s.add_argument("remote", nargs="?", default="origin")
    s.set_defaults(f=cmd_push_notes)
    s = sub.add_parser("collect", help="gather ledger, logs, trailers and notes into one directory")
    s.add_argument("--out", default=os.path.expanduser("~/Desktop"))
    s.set_defaults(f=cmd_collect)
    s = sub.add_parser("summarize", help="metrics from a collected directory")
    s.add_argument("dir")
    s.set_defaults(f=cmd_summarize)
    s = sub.add_parser("selftest", help="prove the hooks work here without touching a real repo")
    s.add_argument(
        "--agent",
        default="claude",
        help="claude|codex|gemini|cursor|copilot|factory or an external gitvow-agent-<name>",
    )
    s.set_defaults(f=cmd_selftest)
    a = p.parse_args(argv)
    return a.f(a)


if __name__ == "__main__":
    sys.exit(main())
