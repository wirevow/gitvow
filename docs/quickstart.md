# Quick start

Requirements: git, Python 3.9 or newer, Claude Code.

## Install for yourself

```sh
pip install provkit
provkit install --user
```

This puts the hooks into your Claude Code user settings, a default policy into `~/.provkit/policy.json`, and a git hook into `~/.provkit/git-hooks` registered as your global hooks path. Every repository you open in Claude Code is covered.

## Prove it works

```sh
provkit selftest
```

The self-check creates a throwaway repository, drives every hook with the payloads Claude Code sends, and prints one line per check. Nothing you own is touched.

```
  ok   session recorded in .git
  ok   deny: force push blocked
  ok   confirm: git push requires asking
  ok   allow: harmless command
  ok   confirm: gate-bearing file edit
  ok   deny: destructive MCP tool
  ok   commit trailer added
  ok   session note attached
  ok   ledger written

selftest: 9 passed, 0 failed
```

## Work normally

Start a Claude Code session in any repository. When the agent commits, look at the result:

```sh
git log -1 --format=%B
provkit show HEAD
```

## Try the gate by hand

```sh
provkit check -- git push --force      # DENY: force push
provkit check -- git push origin main  # CONFIRM: pushing to a remote
provkit check --path values/production-in/api/values.yaml
```

## Remove it

```sh
provkit uninstall --user
```

Everything the install added is removed. Trailers already in commit history stay, because they are part of the commits.
