# Restore what the agent had

## List a session's snapshots

```sh
$ gitvow snapshots
session 8f3d5c71   12 snapshots
  n  taken                tool   file                              changed vs HEAD
  1  2026-09-09 10:44:52  Edit   calc.py                           1 file
  2  2026-09-09 10:45:10  Write  tests/test_calc.py                2 files
  ...
```

`gitvow snapshots --session <id>` narrows to one session; `--all` lists every session in the repository.

## See what the agent had changed at that point

```sh
gitvow diff 8f3d5c71 2            # git diff --stat HEAD..snapshot
gitvow diff 8f3d5c71 2 --full    # the full patch
```

## Restore into a scratch worktree

```sh
$ gitvow restore 8f3d5c71 2
restored refs/gitvow/snapshots/8f3d5c71.../2 into /tmp/gitvow-restore/8f3d5c71-2 (detached worktree)
```

Your branch and working tree are untouched. Read, run or copy from the worktree, then remove it with `git worktree remove <dir>`. Pass `--to <dir>` to choose the location.

## Prune

```sh
gitvow snapshots prune --older-than 14d
gitvow snapshots prune --session <id>
```

## Turn off, or exclude more
In `.gitvow/policy.json` or `~/.gitvow/policy.json`:

```json
"snapshots": {"enabled": true, "max_per_session": 200, "exclude": [".env*", "*.pem", "*.key", "*secret*", "*credential*", ".gitvow/**", ".claude/**"]}
```

`exclude` patterns are git pathspecs applied on top of `.gitignore`; add your own for anything that must never enter a snapshot.
