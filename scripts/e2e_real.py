#!/usr/bin/env python3
"""The real-harness check: drive Claude Code itself, with gitvow's hooks, across two repositories, and read the record.

Every defect found on 2026-09-22 lived in the surface the unit tests never touch: a real agent harness, dispatching
tool calls in parallel, across repositories, through git hooks, on an upgraded install. This script exercises that
surface. It is slow (a minute or two), spends a few thousand tokens on the operator's own Claude account, and is
run by a person or a nightly job, never by the ordinary test suite.

What it does, in a throwaway home:
  1. two repositories, A (the session's working directory) and B (another checkout), each with a bare "origin";
  2. gitvow from this checkout installed at repository scope in both, and its hooks written into an isolated
     Claude Code config directory (CLAUDE_CONFIG_DIR), so the operator's own settings are never touched;
  3. Claude Code in print mode, permissions bypassed (it is a sandbox), given a script of edits and commits that
     crosses repositories and ends with a push to a protected branch;
  4. assertions on the record: trailers, notes, findings in the right repository, the push stopped, the ledger.

Exit 0 when every assertion holds; 1 with a list of what did not. Prints a JSON summary with --json.

Usage:
  scripts/e2e_real.py [--keep] [--json] [--model MODEL]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GITVOW = os.path.join(ROOT, ".venv", "bin", "gitvow")
PY = os.path.join(ROOT, ".venv", "bin", "python")


def sh(
    args: list[str], cwd: str | None = None, env: dict | None = None, check: bool = True, inp: str | None = None
) -> str:
    r = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, input=inp)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed ({r.returncode}): {r.stderr.strip()[:400]}")
    return r.stdout


def git(repo: str, *args: str, env: dict | None = None) -> str:
    return sh(["git", "-C", repo, *args], env=env).strip()


def oauth_token() -> str | None:
    """The operator's Claude Code credential for the isolated run: the environment first, then the macOS keychain
    item Claude Code itself writes. Read, used, and never printed or stored anywhere else."""
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    if sys.platform != "darwin":
        return None
    r = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"], capture_output=True, text=True
    )
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout).get("claudeAiOauth", {}).get("accessToken") or None
    except ValueError:
        return None


def make_repo(root: str, name: str, env: dict) -> tuple[str, str]:
    repo = os.path.join(root, name)
    bare = os.path.join(root, f"{name}.git")
    os.makedirs(repo)
    sh(["git", "init", "-q", "--bare", bare], env=env)
    sh(["git", "init", "-q", "-b", "main", repo], env=env)
    git(repo, "config", "user.email", "e2e@gitvow.test", env=env)
    git(repo, "config", "user.name", "e2e", env=env)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write(f"# {name}\n")
    git(repo, "add", "README.md", env=env)
    git(repo, "commit", "-qm", "init", env=env)
    git(repo, "remote", "add", "origin", bare, env=env)
    git(repo, "push", "-q", "origin", "main", env=env)
    return repo, bare


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the throwaway home for inspection")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model", default="sonnet", help="model for the run; the check is about hooks, not the model")
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        help="Claude Code permission mode for the run. bypassPermissions (default) proves a confirm is a hard block "
        "when nobody can answer; default proves the native question is asked and, unanswered in print mode, stops "
        "the call without recording an approval",
    )
    a = ap.parse_args()

    if not os.path.exists(GITVOW):
        print(f"no {GITVOW}; run `pip install -e .` in {ROOT} first", file=sys.stderr)
        return 2
    if not shutil.which("claude"):
        print("claude (Claude Code) is not on PATH", file=sys.stderr)
        return 2

    token = oauth_token()
    if not token:
        print(
            "no Claude Code credential found: set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) or be logged in "
            "to Claude Code on this machine",
            file=sys.stderr,
        )
        return 2

    home = tempfile.mkdtemp(prefix="gitvow-e2e-")
    cfg = os.path.join(home, "claude")
    os.makedirs(cfg)
    # An isolated config directory keeps the operator's own settings and hooks out of the run; the credential goes
    # in through the environment of the child process only, is never written to disk here and never printed.
    env = {
        **os.environ,
        "HOME": home,
        "CLAUDE_CONFIG_DIR": cfg,
        "CLAUDE_CODE_OAUTH_TOKEN": token,
        "GIT_CONFIG_GLOBAL": os.path.join(home, ".gitconfig"),
    }
    with open(env["GIT_CONFIG_GLOBAL"], "w") as fh:
        fh.write("[user]\n\tname = e2e\n\temail = e2e@gitvow.test\n")

    a_repo, _a_bare = make_repo(home, "alpha", env)
    b_repo, b_bare = make_repo(home, "beta", env)
    os.makedirs(os.path.join(b_repo, "values", "production-in"))
    with open(os.path.join(b_repo, "values", "production-in", "app.yaml"), "w") as fh:
        fh.write("replicas: 1\n")
    git(b_repo, "add", "-A", env=env)
    git(b_repo, "commit", "-qm", "values", env=env)
    git(b_repo, "push", "-q", "origin", "main", env=env)

    # gitvow from this checkout: policy and git hooks at repository scope in both, and the agent hooks once, in the
    # isolated Claude config. The repository-scope agent settings are removed so the hooks are not also picked up
    # from there (that would run every hook twice; 0.28.5 dedupes by tool_use_id, and this keeps the check honest).
    for r in (a_repo, b_repo):
        sh([GITVOW, "install", "--agent", "claude", r], env=env)
        shutil.rmtree(os.path.join(r, ".claude"), ignore_errors=True)
    sh(
        [
            PY,
            "-c",
            f"from gitvow.install import merge_settings; merge_settings({os.path.join(cfg, 'settings.json')!r}, {GITVOW!r})",
        ],
        env=env,
    )

    prompt = f"""You are testing a git tool in a throwaway sandbox. Do exactly these steps, in order, with these exact commands, and never work around a refusal.
1. Append the line "e2e edit" to README.md in the current repository ({a_repo}).
2. Edit {b_repo}/values/production-in/app.yaml and change replicas to 2.
3. Run: git commit -am "e2e: alpha readme"
4. Run: git -C {b_repo} commit -am "e2e: beta replicas"
5. Run: git -C {b_repo} push origin main
Rules: if a command is BLOCKED, stop and reply with the message verbatim. If a command shows a card or says CONFIRMATION REQUIRED, run the exact same command once more; if it is still not allowed, stop and reply with that message verbatim. When all five steps are done, reply "done"."""
    t0 = time.time()
    cmd = [
        "claude",
        "-p",
        prompt,
        "--permission-mode",
        a.permission_mode,
        "--max-turns",
        str(a.max_turns),
        "--output-format",
        "json",
    ]
    if a.model:
        cmd += ["--model", a.model]
    if a.permission_mode != "bypassPermissions":
        # In a prompting mode, print mode cannot answer Claude Code's own permission questions, so pre-approve the
        # tools the script needs. gitvow's `ask` for the push is then the only question left, and it goes
        # unanswered: the push must not happen and no approval may be recorded.
        cmd += ["--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", "Bash(git:*)"]
    r = subprocess.run(cmd, cwd=a_repo, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {"result": r.stdout[-800:], "is_error": True, "stderr": r.stderr[-800:]}
    elapsed = round(time.time() - t0, 1)

    # --- assertions on the record ------------------------------------------------------------------------
    fails: list[str] = []

    def check(cond: bool, what: str) -> None:
        if not cond:
            fails.append(what)

    a_body = git(a_repo, "log", "-1", "--format=%B", env=env)
    b_body = git(b_repo, "log", "-1", "--format=%B", env=env)
    check("e2e: alpha readme" in a_body, "alpha commit made by the agent")
    check("Gitvow-Session:" in a_body, "alpha commit carries a session trailer")
    check("e2e: beta replicas" in b_body, "beta commit made by the agent through git -C")
    check("Gitvow-Session:" in b_body, "beta commit carries a session trailer (target-repository routing)")
    check(
        "Gitvow-Open: edit values/production-in/app.yaml" in b_body,
        "beta commit carries the open finding for the production values edit",
    )
    notes_a = git(a_repo, "for-each-ref", "refs/notes/gitvow/", env=env)
    notes_b = git(b_repo, "for-each-ref", "refs/notes/gitvow/", env=env)
    check(bool(notes_a), "alpha has a session note ref")
    check(bool(notes_b), "beta has a session note ref")
    b_remote_main = git(b_bare, "rev-parse", "main", env=env)
    b_local_main = git(b_repo, "rev-parse", "main", env=env)
    check(b_remote_main != b_local_main, "the push to beta's protected branch did not happen")
    b_log = ""
    p = os.path.join(b_repo, ".git", "gitvow-hooks.log")
    if os.path.exists(p):
        with open(p) as fh:
            b_log = fh.read()
    check(
        '"confirm_required"' in b_log and "pushing to a protected branch" in b_log,
        "beta's hook log shows the push was asked about",
    )
    check('"finding"' in b_log or '"observed"' in b_log, "beta's hook log recorded the production values finding")
    check('"kind": "card"' in b_log, "beta's hook log shows the card at the agent's commit")
    check(b_log.count('"kind": "card"') == 1, "the card was shown once (hooks did not run twice)")
    a_log = ""
    p = os.path.join(a_repo, ".git", "gitvow-hooks.log")
    if os.path.exists(p):
        with open(p) as fh:
            a_log = fh.read()
    check(a_log.count('"kind": "session_start"') == 1, "session start recorded once in alpha (hooks did not run twice)")
    check(
        "Gitvow-Open: edit values/production-in/app.yaml" in b_body,
        "beta commit carries the open finding as Gitvow-Open",
    )
    a_state = {}
    p = os.path.join(a_repo, ".git", "gitvow-session.json")
    if os.path.exists(p):
        with open(p) as fh:
            a_state = json.load(fh)
    touched = {os.path.realpath(t) for t in (a_state.get("repos_touched") or [])}
    check(os.path.realpath(b_repo) in touched, "alpha's session state names beta as a repository it reached")
    result_text = str(out.get("result", ""))
    check(
        "protected branch" in result_text or "CONFIRMATION" in result_text.upper(),
        "the agent reported the confirmation message rather than working around it",
    )

    summary = {
        "ok": not fails,
        "failed": fails,
        "elapsed_seconds": elapsed,
        "claude": {
            "is_error": out.get("is_error"),
            "num_turns": out.get("num_turns"),
            "cost_usd": out.get("total_cost_usd"),
            "result_head": result_text[:200],
        },
        "home": home if a.keep else None,
    }
    if a.json:
        print(json.dumps(summary, indent=1))
    else:
        print(
            f"real-harness check: {'OK' if not fails else 'FAILED'} in {elapsed}s, {out.get('num_turns')} turns, ${out.get('total_cost_usd')}"
        )
        for f in fails:
            print(f"  FAIL {f}")
        if a.keep:
            print(f"kept: {home}")
    if not a.keep:
        shutil.rmtree(home, ignore_errors=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
