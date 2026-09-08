# Install per user or per repo

## Per user

```sh
provkit install --user
```

- Default policy → `~/.provkit/policy.json` (created only if absent, so your edits survive reinstalls)
- Git hook → `~/.provkit/git-hooks/prepare-commit-msg`, registered as the global `core.hooksPath`
- Hook entries merged into `~/.claude/settings.json`

Covers every repository you open. Nothing is committed anywhere. If you already had a global hooks path, provkit reports it; its hook chains to each repository's own `.git/hooks/prepare-commit-msg`, not to a previous global path.

## Per repo

```sh
provkit install /path/to/repo
```

- Policy and git hook → `<repo>/.provkit/`
- Hook entries merged into `<repo>/.claude/settings.json`
- `core.hooksPath` set to `.provkit/git-hooks` for that repository

Commit `.provkit/` and `.claude/settings.json` to share. Each teammate runs once: `git config core.hooksPath .provkit/git-hooks`. Use this once a team has agreed a policy: it is reviewed in pull requests, and gate paths are specific to the repository.

## Precedence
Policy lookup: `<repo>/.provkit/policy.json`, then `~/.provkit/policy.json`, then the package default. Hooks in both user and repository settings run; provkit's entries are idempotent, so installing twice never duplicates them and never disturbs hooks you added yourself.

## Uninstall

```sh
provkit uninstall --user [--purge-policy] [--purge-ledger]
provkit uninstall /path/to/repo [--purge-notes]
```

Removes exactly what install added. Leaves commit trailers already in history, notes already pushed, and the ledger unless asked.

## Pause without uninstalling
`git config --global --unset core.hooksPath` stops trailers. The gate keeps running until the hooks are removed. To relax the gate temporarily, edit the policy lists rather than removing the file: a missing policy fails closed.
