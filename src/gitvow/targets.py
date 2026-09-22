"""What a command reaches, read from the environment before it runs.

A rule on text cannot tell `kubectl apply` against a kind cluster from the same string against production. The
target can: the `--context` flag or the kubeconfig's current context, the remote a `git push` goes to and the
branch it lands on, the terraform workspace under `-chdir`. Each is read deterministically, offline, from files
and flags already on the machine, in a few milliseconds, and only once a `{program, verbs}` rule has matched.

Everything here returns a short string a policy `target` regex can match (`context=prod-in namespace=billing`),
or None when the target cannot be read. The caller treats None as matching every `target` rule: an action
whose reach cannot be established is asked about, not waved through.
"""

from __future__ import annotations

import os
import re
from typing import Any

from .state import git


def _flag(args: list[str], *names: str) -> str | None:
    """The value of `--name v`, `--name=v` or `-n v` for any of the given names."""
    for i, a in enumerate(args):
        for n in names:
            if a == n and i + 1 < len(args):
                return args[i + 1]
            if a.startswith(n + "="):
                return a[len(n) + 1 :]
    return None


def _kubeconfig_current_context(explicit: str | None, env: dict[str, str]) -> str | None:
    paths = (
        [explicit]
        if explicit
        else (env.get("KUBECONFIG") or os.path.join(env.get("HOME", "~"), ".kube", "config")).split(os.pathsep)
    )
    for p in paths:
        p = os.path.expanduser(p)
        try:
            with open(p) as fh:
                for ln in fh:
                    m = re.match(r"^current-context:\s*['\"]?([^'\"\s]+)", ln)
                    if m:
                        return m.group(1)
        except OSError:
            continue
    return None


def kubectl(args: list[str], env: dict[str, str]) -> str | None:
    ctx = _flag(args, "--context") or _kubeconfig_current_context(_flag(args, "--kubeconfig"), env)
    ns = _flag(args, "--namespace", "-n")
    if not ctx:
        return None
    return f"context={ctx}" + (f" namespace={ns}" if ns else "")


def helm(args: list[str], env: dict[str, str]) -> str | None:
    ctx = _flag(args, "--kube-context") or _kubeconfig_current_context(_flag(args, "--kubeconfig"), env)
    ns = _flag(args, "--namespace", "-n")
    if not ctx:
        return None
    return f"context={ctx}" + (f" namespace={ns}" if ns else "")


def git_push(args: list[str], cwd: str | None) -> str | None:
    """`remote=<url> branch=<name>` for a push; the remote is named or `origin`, the branch is the refspec's
    destination or the current branch."""
    positional = [a for a in args if not a.startswith("-")]
    remote = positional[0] if positional else "origin"
    branch = None
    if len(positional) > 1:
        spec = positional[1]
        branch = spec.split(":", 1)[1] if ":" in spec else spec
        branch = branch.replace("refs/heads/", "")
    if not cwd:
        return None
    rc, url, _ = git(["remote", "get-url", remote], cwd)
    if rc != 0:
        return None
    if not branch:
        rc, cur, _ = git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
        branch = cur if rc == 0 else None
    return f"remote={url}" + (f" branch={branch}" if branch else "")


def terraform(args: list[str], cwd: str | None) -> str | None:
    d = _flag(args, "-chdir") or "."
    base = d if os.path.isabs(d) else os.path.join(cwd or ".", d)
    ws = "default"
    try:
        with open(os.path.join(base, ".terraform", "environment")) as fh:
            ws = fh.read().strip() or "default"
    except OSError:
        pass
    return f"workspace={ws} dir={os.path.normpath(d)}"


def resolve(prog: str, args: list[str], cwd: str | None, env: dict[str, str] | None = None) -> str | None:
    """The target of `prog args`, or None when it cannot be read."""
    env = env if env is not None else dict(os.environ)
    try:
        if prog == "kubectl":
            return kubectl(args, env)
        if prog == "helm":
            return helm(args, env)
        if prog == "git":
            # step over git's own options (`-C dir`, `-c k=v`, `--git-dir=…`) to the subcommand
            i = 0
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
            if i < len(args) and args[i] == "push":
                repo_dir = None
                for j in range(0, i):
                    if args[j] == "-C" and j + 1 < len(args):
                        repo_dir = args[j + 1]
                return git_push(args[i + 1 :], repo_dir if repo_dir and os.path.isdir(repo_dir) else cwd)
        if prog in ("terraform", "tofu"):
            return terraform(args, cwd)
    except Exception:  # a target resolver must never take the gate down; None asks
        return None
    return None


def target_matches(rule: dict[str, Any], target: str | None) -> bool:
    """A rule without `target` matches everything. A rule with one matches its regex, and matches an unreadable
    target too, so an action whose reach is unknown is treated as reaching the place the rule protects."""
    pat = rule.get("target")
    if not pat:
        return True
    if target is None:
        return True
    return re.search(pat, target) is not None
