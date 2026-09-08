# Quick start

Requirements: git, Python 3.9 or newer, Claude Code.

## Install for yourself

```sh
pip install gitvow
gitvow install --user
```

This puts the hooks into your Claude Code user settings, a default policy into `~/.gitvow/policy.json`, and a git hook into `~/.gitvow/git-hooks` registered as your global hooks path. Every repository you open in Claude Code is covered.

## Prove it works

```sh
gitvow selftest
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
gitvow show HEAD
```

## Try the gate by hand

```sh
gitvow check -- git push --force      # DENY: force push
gitvow check -- git push origin main  # CONFIRM: pushing to a remote
gitvow check --path values/production-in/api/values.yaml
```

## Remove it

```sh
gitvow uninstall --user
```

Everything the install added is removed. Trailers already in commit history stay, because they are part of the commits.
