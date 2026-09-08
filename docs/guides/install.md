# Install per user or per repo

## Per user

```sh
gitvow install --user
```

- Default policy → `~/.gitvow/policy.json` (created only if absent, so your edits survive reinstalls)
- Git hook → `~/.gitvow/git-hooks/prepare-commit-msg`, registered as the global `core.hooksPath`
- Hook entries merged into `~/.claude/settings.json`, using the **absolute path** of the `gitvow` executable, so hooks work even when Claude Code's shell has no virtualenv or pipx directory on its PATH
- Global git config `notes.displayRef` and `notes.rewriteRef` set to `refs/notes/gitvow/*`, so `git log --show-notes` shows session notes and they follow amend, rebase and squash

Covers every repository you open. Nothing is committed anywhere. If you already had a global hooks path, gitvow reports it; its hook chains to each repository's own `.git/hooks/prepare-commit-msg`, not to a previous global path.

## Per repo

```sh
gitvow install /path/to/repo
```

- Policy and git hook → `<repo>/.gitvow/`
- Hook entries merged into `<repo>/.claude/settings.json`, using the bare command `gitvow` because the file is shared by people with different install paths; every teammate needs `gitvow` on the PATH their shell gives Claude Code
- `core.hooksPath` set to `.gitvow/git-hooks` for that repository
- Repository git config `notes.displayRef` and `notes.rewriteRef` set to `refs/notes/gitvow/*`

Commit `.gitvow/` and `.claude/settings.json` to share. Each teammate runs once: `git config core.hooksPath .gitvow/git-hooks`. Use this once a team has agreed a policy: it is reviewed in pull requests, and gate paths are specific to the repository.

## Precedence
Policy lookup: `<repo>/.gitvow/policy.json`, then `~/.gitvow/policy.json`, then the package default. Hooks in both user and repository settings run; gitvow's entries are idempotent, so installing twice never duplicates them and never disturbs hooks you added yourself.

## Uninstall

```sh
gitvow uninstall --user [--purge-policy] [--purge-ledger]
gitvow uninstall /path/to/repo [--purge-notes]
```

Removes exactly what install added, including the git config keys when they still hold gitvow's values. Leaves commit trailers already in history, notes already pushed, and the ledger unless asked. `--purge-notes` deletes every local `refs/notes/gitvow/*` ref.

## If hooks silently do nothing
Claude Code treats a hook command it cannot run as a non-blocking error and continues, which would switch the gate off without a visible failure. Check with:

```sh
gitvow selftest                       # the package works
grep -o '"command": "[^"]*"' ~/.claude/settings.json .claude/settings.json 2>/dev/null
```

If the recorded command is a bare `gitvow` and your install lives in a virtualenv, either install with `pipx install gitvow` or `pip install --user gitvow` so it is on the PATH, or reinstall per user so the absolute path is recorded. Claude Code's `/hooks` view also shows hook errors.

## Pause without uninstalling
`git config --global --unset core.hooksPath` stops trailers. The gate keeps running until the hooks are removed. To relax the gate temporarily, edit the policy lists rather than removing the file: a missing policy fails closed.
