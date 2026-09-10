# Install per user or per repo

## Per user

```sh
gitvow install --user            # every agent found on this machine
gitvow install --user --check    # the same, then run the self-check
gitvow install --user --agent codex   # just one
```

With no `--agent`, install looks for each agent by its configuration directory, its application path and its command, configures every one it finds, and prints the list. Forgetting the flag used to configure Claude Code alone and leave the rest ungated, which is indistinguishable from not installing at all.

- Default policy → `~/.gitvow/policy.json` (created only if absent, so your edits survive reinstalls)
- Four git hooks → `~/.gitvow/git-hooks/` (`prepare-commit-msg`, `pre-commit`, `post-commit`, `pre-push`), registered as the global `core.hooksPath`
- Hook entries merged into each agent's settings file, using the **absolute path** of the `gitvow` executable, so hooks work even when the agent's shell has no virtualenv or pipx directory on its PATH. This matters most for desktop agents such as Cursor, which do not inherit the PATH of your terminal at all.
- Whatever the agent still needs by hand, printed at the end. Codex CLI has two such steps; see [Use gitvow with other agents](other-agents.md).
- A `pre-push` hook in the same directory that pushes `refs/notes/gitvow/*` to the remote you push to
- Global git config `notes.displayRef` and `notes.rewriteRef` set to `refs/notes/gitvow/*`, so `git log --show-notes` shows session notes and they follow amend, rebase and squash

Covers every repository you open. Nothing is committed anywhere. If you already had a global hooks path, gitvow reports it; its hook chains to each repository's own `.git/hooks/prepare-commit-msg`, not to a previous global path.

## Per repo

```sh
gitvow install /path/to/repo
```

- Policy and git hook → `<repo>/.gitvow/`
- Hook entries merged into `<repo>/.claude/settings.json` (or that agent's file), using the bare command `gitvow` because the file is shared by people with different install paths; every teammate needs `gitvow` on the PATH their agent's shell gives it. A desktop agent started from Finder or Spotlight often has a minimal PATH, so a per-user install alongside is safer there. `gitvow status` reports this as a note rather than leaving you to find out.
- `core.hooksPath` set to `.gitvow/git-hooks` for that repository
- Repository git config `notes.displayRef` and `notes.rewriteRef` set to `refs/notes/gitvow/*`
- A `pre-push` hook that pushes session notes with every push

Commit `.gitvow/` and `.claude/settings.json` to share. Each teammate runs once: `git config core.hooksPath .gitvow/git-hooks`. Use this once a team has agreed a policy: it is reviewed in pull requests, and gate paths are specific to the repository.

## Precedence
Policy lookup: `<repo>/.gitvow/policy.json`, then `~/.gitvow/policy.json`, then the package default. Hooks in both user and repository settings run; gitvow's entries are idempotent, so installing twice never duplicates them and never disturbs hooks you added yourself.

## Upgrading
`pip install --upgrade gitvow` replaces the package but not the git hook files written by `install`, nor the hook commands in settings. After upgrading, re-run the same install command you used (`gitvow install --user` or `gitvow install <repo>`). It is idempotent and refreshes both.

`gitvow status` detects an install left behind by an older release: git hooks whose contents differ from what this version writes, and agent settings missing events this version installs. Both matter. An install from before 0.12 has no `pre-commit` or `post-commit` hook, so the decision card never fires on a commit made by a person, and nothing else would tell you. Repositories that committed `.gitvow/` refresh it in a pull request like any other change.

## Uninstall

```sh
gitvow uninstall --user [--purge-policy] [--purge-ledger]
gitvow uninstall /path/to/repo [--purge-notes]
```

Removes exactly what install added, including the git config keys when they still hold gitvow's values. Leaves commit trailers already in history, notes already pushed, and the ledger unless asked. `--purge-notes` deletes every local `refs/notes/gitvow/*` ref.

## If hooks silently do nothing
Most agents treat a hook command they cannot run as a non-blocking error and carry on, which switches the gate off with no visible failure. That is the worst outcome, so there is a command for exactly this:

```sh
gitvow status     # inside the repository; exits non-zero when something is wrong
gitvow selftest   # proves the package itself works, in a throwaway repository
```

`status` resolves the command each agent will actually run and fails when it cannot be executed. If the recorded command is a bare `gitvow` and your install lives in a virtualenv, either install with `pipx install gitvow` or `pip install --user gitvow` so it is on the PATH everywhere, or re-run the per-user install so the absolute path is recorded. Claude Code's `/hooks` view also shows hook errors, and Codex needs its hooks trusted before they run at all.

## Pause without uninstalling
`git config --global --unset core.hooksPath` stops trailers. The gate keeps running until the hooks are removed. To relax the gate temporarily, edit the policy lists rather than removing the file: a missing policy fails closed.
