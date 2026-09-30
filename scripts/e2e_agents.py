#!/usr/bin/env python3
"""The real-harness check for the agents that are not Claude Code: OpenCode and Gemini CLI, live, nightly.

Both were first verified by hand on 2026-09-29 and both taught us something the unit tests could not (OpenCode hands
the first message over quoted; Gemini runs git with its hooks disabled). This script repeats those sessions every
night so "verified" stays true of today rather than of one afternoon.

For each agent, in a throwaway HOME so the operator's own configuration is never touched:
  1. a repository with a bare "origin", gitvow from this checkout installed at repository scope for the agent;
  2. session one: an edit, then `git push --force origin main`, which the gate must refuse;
  3. session two: a gated file, then `git add -A && git commit`, which the card must stop once and the retry
     must commit with the session trailer and the open finding, and a note;
  4. assertions on the record: the refusal in the log, the card once, the trailers, the note, the snapshot, the
     session's end.

OpenCode runs on one of its free models and needs no sign-in. Gemini needs a key: the operator's `~/.gemini/.env`
is copied into the throwaway HOME (never read by this script, never printed) and the folder is trusted for headless
use. Exit 0 when every assertion holds for the agents run; 1 with the list of what did not. `--json` prints a summary.

Usage:
  scripts/e2e_agents.py [--agent opencode|gemini] [--keep] [--json] [--opencode-model M] [--gemini-model M]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "bin", "python")
CMD_PREFIX = f"{PY} -m gitvow"

PROMPT_ONE = (
    "You are testing a git tool in a throwaway sandbox. Do exactly these steps in order and never work around a "
    "refusal. 1) Append the line 'e2e edit' to README.md using your file tools. 2) Run the shell command: "
    "git push --force origin main. If a tool call is blocked or errors, stop and reply with the error message "
    "verbatim. When done reply done."
)
PROMPT_TWO = (
    "Sandbox test, follow exactly. 1) Create the file .github/workflows/ci.yml containing the single line: "
    "name: ci  2) Run the shell command: git add -A && git commit -m 'e2e ci'  If the commit is refused with a "
    "card that says OPEN FINDINGS, run the exact same commit command once more. If anything else is blocked, stop "
    "and reply with the message verbatim. When done reply done."
)


def sh(
    args: list[str], cwd: str | None = None, env: dict | None = None, timeout: int = 600
) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout
    )


def git(repo: str, *args: str, env: dict | None = None) -> str:
    return sh(["git", "-C", repo, *args], env=env).stdout.strip()


def make_repo(root: str, env: dict) -> str:
    repo = os.path.join(root, "sandbox")
    bare = os.path.join(root, "sandbox.git")
    os.makedirs(repo)
    sh(["git", "init", "-q", "--bare", bare], env=env)
    sh(["git", "init", "-q", "-b", "main", repo], env=env)
    git(repo, "config", "user.email", "e2e@gitvow.test", env=env)
    git(repo, "config", "user.name", "e2e", env=env)
    with open(os.path.join(repo, "README.md"), "w") as fh:
        fh.write("# sandbox\n")
    git(repo, "add", "README.md", env=env)
    git(repo, "commit", "-qm", "init", env=env)
    git(repo, "remote", "add", "origin", bare, env=env)
    git(repo, "push", "-q", "origin", "main", env=env)
    return repo


def install(repo: str, agent: str, env: dict) -> None:
    code = f"from gitvow.install import install_repo; install_repo({repo!r}, {CMD_PREFIX!r}, {agent!r})"
    r = sh([PY, "-c", code], env=env)
    if r.returncode != 0:
        raise RuntimeError(f"install failed: {r.stderr[:300]}")
    git(repo, "add", "-A", env=env)
    git(repo, "commit", "-qm", "gitvow install", env=env)


PROMPT_RETRY = "Now run the exact same commit command once more: git add -A && git commit -m 'e2e ci'  Then reply done."


def run_agent(
    agent: str, repo: str, prompt: str, env: dict, model: str, continue_: bool = False
) -> subprocess.CompletedProcess:
    # OpenCode takes its project from $PWD, not the process's working directory: the first nightly-style run
    # inherited the harness's PWD and edited and committed in *this checkout* instead of the sandbox. Say the
    # directory every way an agent might read it.
    env = {**env, "PWD": repo}
    if agent == "opencode":
        more = ["--continue"] if continue_ else []
        return sh(["opencode", "run", "--dir", repo, *more, "--model", model, prompt], cwd=repo, env=env)
    return sh(["gemini", "-m", model, "-p", prompt, "--approval-mode", "yolo"], cwd=repo, env=env)


def hook_events(repo: str) -> list[dict]:
    p = os.path.join(repo, ".git", "gitvow-hooks.log")
    out: list[dict] = []
    if os.path.exists(p):
        with open(p) as fh:
            for ln in fh:
                with contextlib.suppress(ValueError):
                    out.append(json.loads(ln))
    return out


def check_agent(agent: str, model: str, keep: bool) -> dict:
    home = tempfile.mkdtemp(prefix=f"gitvow-e2e-{agent}-")
    env = {
        **os.environ,
        "HOME": home,
        "XDG_CONFIG_HOME": os.path.join(home, ".config"),
        "XDG_DATA_HOME": os.path.join(home, ".local", "share"),
        "GIT_CONFIG_GLOBAL": os.path.join(home, ".gitconfig"),
        "GEMINI_CLI_TRUST_WORKSPACE": "true",
        # OpenCode loads plugins only once its package cache exists; a fresh home has none and the plugin is
        # skipped without a word (found on the first nightly-style run). The operator's cache holds no secrets.
        "XDG_CACHE_HOME": os.path.join(os.path.expanduser("~"), ".cache"),
    }
    with open(env["GIT_CONFIG_GLOBAL"], "w") as fh:
        fh.write("[user]\n\temail = e2e@gitvow.test\n\tname = e2e\n")
    fails: list[str] = []
    notes = ""
    tails: dict[str, str] = {}
    t0 = time.time()
    try:
        if agent == "gemini":
            key = os.path.expanduser("~/.gemini/.env")
            if not os.path.exists(key):
                return {
                    "agent": agent,
                    "ok": None,
                    "skipped": "no ~/.gemini/.env with a key; Gemini cannot run headless",
                }
            os.makedirs(os.path.join(home, ".gemini"))
            shutil.copy(key, os.path.join(home, ".gemini", ".env"))  # copied, never read or printed
        if shutil.which(agent if agent == "gemini" else "opencode") is None:
            return {"agent": agent, "ok": None, "skipped": f"{agent} is not installed"}
        if agent == "opencode":
            # On first sight of a plugin OpenCode writes a package.json for its plugin SDK and installs it with bun,
            # a network step that took minutes or hung in a fresh home and left the plugin unloaded. Seed the
            # throwaway config directory from the operator's, which already has it; nothing in it is a secret.
            real = os.path.join(os.path.expanduser("~"), ".config", "opencode")
            mine = os.path.join(home, ".config", "opencode")
            os.makedirs(mine, exist_ok=True)
            for name in ("package.json", "package-lock.json", "opencode.jsonc", "opencode.json"):
                if os.path.exists(os.path.join(real, name)):
                    shutil.copy(os.path.join(real, name), os.path.join(mine, name))
            if os.path.isdir(os.path.join(real, "node_modules")):
                os.symlink(os.path.join(real, "node_modules"), os.path.join(mine, "node_modules"))
        repo = make_repo(home, env)
        install(repo, agent, env)

        r1 = run_agent(agent, repo, PROMPT_ONE, env, model)
        text1 = r1.stdout + r1.stderr
        if "429" in text1 and "free_tier" in text1:
            # the provider's free quota is spent for today: not a gitvow failure, and nothing below can be judged
            return {"agent": agent, "model": model, "ok": None, "skipped": "rate limited by the provider's free tier"}
        if r1.returncode != 0 and "BLOCKED" not in text1:
            fails.append(f"session one did not complete: {text1[-300:]}")
        r2 = run_agent(agent, repo, PROMPT_TWO, env, model)
        text2 = r2.stdout + r2.stderr
        tails = {"one": text1[-600:], "two": text2[-600:]}
        if (
            agent == "opencode"
            and "OPEN FINDINGS" in text2
            and git(repo, "log", "-1", "--format=%s", env=env) != "e2e ci"
        ):
            # The free model sometimes stops at the card instead of retrying as told. Continue the same session
            # with the retry, which is what a person would type; the card's acknowledgement belongs to the session.
            r3 = run_agent(agent, repo, PROMPT_RETRY, env, model, continue_=True)
            tails["retry"] = (r3.stdout + r3.stderr)[-600:]

        # the sessions must have worked in the sandbox and nowhere else
        if git(ROOT, "status", "--porcelain", "--", "README.md", ".github/workflows/ci.yml", env=os.environ.copy()):
            fails.append("the agent touched this checkout instead of the sandbox; stopping")
            return {"agent": agent, "model": model, "ok": False, "failed": fails, "home": home}
        events = hook_events(repo)
        kinds = [e.get("kind") for e in events]
        body = git(repo, "log", "-1", "--format=%B", env=env)
        subject = git(repo, "log", "-1", "--format=%s", env=env)

        def check(cond: bool, what: str) -> None:
            if not cond:
                fails.append(what)

        check("session_start" in kinds, "a session start reached the hook")
        check(
            any(e.get("kind") == "blocked" and "force push" in (e.get("reason") or "") for e in events),
            "the force push was refused by the gate",
        )
        check("BLOCKED" in text1, "the agent saw the refusal (BLOCKED in its output)")
        check("snapshot" in kinds, "a snapshot was taken after the edit")
        check(kinds.count("card") == 1, f"the card was shown once at the commit (seen {kinds.count('card')})")
        check(subject == "e2e ci", f"the retried commit landed (HEAD is {subject!r})")
        check("Gitvow-Session:" in body, "the commit carries a session trailer")
        check("Gitvow-Open: edit .github/workflows/ci.yml" in body, "the commit carries the open finding")
        refs = git(repo, "for-each-ref", "refs/notes/gitvow/", env=env)
        check(bool(refs), "a session note ref exists")
        head = git(repo, "rev-parse", "HEAD", env=env)
        have_note = any(
            sh(
                ["git", "-C", repo, "notes", f"--ref={ln.split()[-1].replace('refs/notes/', '')}", "show", head],
                env=env,
            ).returncode
            == 0
            for ln in refs.splitlines()
        )
        check(have_note, "the commit has a session note")
        check("session_stop" in kinds, "the session's end reached the hook")
        if agent == "gemini":
            check("trailers_repaired" in kinds, "gitvow wrote the trailers itself, because Gemini disables git hooks")
        if agent == "opencode":
            check("intent" in kinds, "the first message became the intent")
        notes = text2[-200:]
    except Exception as e:  # the check must report, not crash
        fails.append(f"exception: {e}")
    finally:
        if not keep:
            shutil.rmtree(home, ignore_errors=True)
    return {
        "agent": agent,
        "model": model,
        "ok": not fails,
        "failed": fails,
        "elapsed_seconds": round(time.time() - t0, 1),
        "home": home if keep else None,
        "tail": notes,
        "sessions": tails,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["opencode", "gemini"], action="append", help="default: both")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--opencode-model", default="opencode/big-pickle")
    ap.add_argument("--gemini-model", default="gemini-3-flash-preview")
    a = ap.parse_args()
    if not os.path.exists(PY):
        print(f"no {PY}; run `pip install -e .` in {ROOT} first", file=sys.stderr)
        return 2
    agents = a.agent or ["opencode", "gemini"]
    results = [check_agent(ag, a.opencode_model if ag == "opencode" else a.gemini_model, a.keep) for ag in agents]
    ok = all(r.get("ok") is not False for r in results)
    if a.json:
        print(json.dumps({"ok": ok, "agents": results}, indent=1))
    else:
        for r in results:
            if r.get("skipped"):
                print(f"{r['agent']}: skipped ({r['skipped']})")
                continue
            print(f"{r['agent']} ({r['model']}): {'OK' if r['ok'] else 'FAILED'} in {r['elapsed_seconds']}s")
            for f in r["failed"]:
                print(f"  FAIL {f}")
            if r.get("home"):
                print(f"  kept: {r['home']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
