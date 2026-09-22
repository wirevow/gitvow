# What gitvow reads, writes, executes and sends

This page states, precisely enough to be checked, what gitvow touches. Every statement matches the 0.26 release
and the code under `src/gitvow/`. [Security and the bypass surface](security.md) is the companion: it says where
enforcement holds and where it does not. This page says what the tool does on your machine and to your
repository, so a reviewer can verify the claim rather than take it.

## Network

gitvow makes no network call of its own. It has no runtime dependencies, no telemetry, no update check and no
hosted API. Three things reach a network, each explicit and each yours:

- `git push` of your branch, and the `pre-push` hook's push of `refs/notes/gitvow/*` to the same remote.
- `gitvow sync`, which delivers an export bundle to the sinks *you* configured in `.gitvow/export.local.json`
  (a directory, a git remote, or an HTTP store the customer runs). Nothing is configured by default; with no
  sink, `sync` builds the bundle and moves nothing.
- A [provider](concepts/providers.md) or [external adapter](reference/adapter-protocol.md) you install may
  call whatever it likes; gitvow runs it as a subprocess and reads its stdout. The gate itself never waits on
  a network call: providers answer from a local cache or not at all.

The LLM classifier in the policy is off unless you turn it on, and when on it is your endpoint and your key.

## What it reads

- **The hook payload** the agent sends: session id, working directory, tool name and tool input. The tool
  input is inspected as text against the policy; a Bash command is read as a string, an edit as a path.
- **Repository state**: `git rev-parse`, `git log`, `git show`, `git notes`, the working tree of a file the
  agent just edited (to store its blob for attribution), ownership and instruction files it manages.
- **Policy and rules**: `.gitvow/policy.json` in the repository, `~/.gitvow/policy.json`, the cached
  organisation pack under `~/.gitvow/cache/packs/`, and `.gitvow/redact-rules.json`. A policy that fails to
  load refuses every tool call rather than allowing them.
- **The agent's transcript**, on the machine, at commit and at session stop, to build the note and the ledger
  entry: tool names, a shortened and redacted argument per call, the last stated plan after redaction, token
  usage. Tool output is never copied anywhere. The transcript itself is never copied anywhere.
- **`~/.gitvow/cache/brief/`**, the store's brief for this repository, fetched by a previous `sync`. Nothing
  is fetched during a tool call.
- **The reach of a matched command**, when a rule names a `target`: the kubeconfig's `current-context` line
  (`--kubeconfig`, `$KUBECONFIG`, or `~/.kube/config`; the file is scanned for that one line and nothing else is
  kept), `git remote get-url` and the current branch, and `.terraform/environment` under `-chdir`. Read only
  after a `{program, verbs}` rule has matched, never for an ordinary command.

### Credential-store paths

A path that names a credential store is excluded from everything gitvow stores, whatever its content:
snapshots never contain it, no attribution blob is written for it, and the hook log records `blob_skipped`
with the path. The list (`src/gitvow/paths.py`) covers `.env` and `.env.*`, `.envrc`, `.npmrc`, `.netrc`,
`.pgpass`, `.htpasswd`, `.pypirc`, `.dockercfg`, `.boto`, `.git-credentials`, `.docker/config.json`,
`.kube/config`, `.aws/credentials`, `credentials.tfrc.json`, `application_default_credentials.json`, SSH
private keys, `credentials.*` and `secrets.*` configuration files, key material by suffix (`.pem`, `.key`,
`.pfx`, `.p12`, `.pkcs12`, `.jks`, `.keystore`, `.truststore`, `.ppk`, `.kdbx`, `.asc`, `.gpg`), and
configuration-shaped files under any directory named `secrets/` or `credentials/`. Matching is by path and
case-insensitive. The list is loaded after the policy's own `snapshots.exclude`, so a policy can add to it and
cannot remove from it.

Classification is by path, never by content. A secret inside `deploy/prod-values.yaml` is not covered here; the
redaction layer is best effort on values and says so. Public halves (`.crt`, `.cer`, `.pub`) are not excluded,
and source under `internal/secrets/` stays visible.

## What it writes

Inside the repository's git directory, never in the tree:

- `.git/gitvow-session.json`: the session's findings, decisions, pending-commit flag, attribution blob ids,
  edits that landed elsewhere, the repositories the session reached.
- `.git/gitvow-hooks.log`: one line per hook event, redacted, appended.
- Loose objects for attribution blobs (`git hash-object -w`), unreachable from any ref, pruned by `git gc`.
- `refs/notes/gitvow/<session>`: the session note per commit.
- `refs/gitvow/snapshots/<session>/<n>`: working-tree snapshots after agent edits, never pushed.
- `.git/info/exclude`: one line so `.gitvow/export.local.json` (your sinks) is never committed.

In the working tree, only through `install`, `rules`, `claims` and `policy`, and only these paths:

- `.gitvow/policy.json`, `.gitvow/git-hooks/*`, `.gitvow/export.local.json`.
- The agent's settings file for a per-repository install (`.claude/settings.json`, `.codex/hooks.json`,
  `.cursor/hooks.json`, `.github/hooks/gitvow.json`, `.factory/hooks.json`, `.gemini/settings.json`): one
  managed entry per hook event, everything else in the file preserved.
- The managed sections in instruction files (`CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`,
  `.cursor/rules/*`): between gitvow's own markers, everything outside preserved.

Every write into a repository is preflighted (`src/gitvow/safewrite.py`): the target must resolve inside the
repository, must not resolve into its git directory (found through `git rev-parse --git-dir`, so a relocated
one counts), must be a regular file or absent, and must not be a hard link, because an inode's other names
cannot be read back and one of them may be `.git/config`. A refused write names the path and the reason and
writes nothing. A checked-in symlink at `.claude/settings.json` therefore cannot aim the install at another
file. Writes are also skipped when the bytes are already current, so a rerun leaves mtimes alone; the install
stamp that `gitvow status` uses to find ungated commits is a hook's mtime, and a rewrite would have hidden them.

Outside the repository: `~/.gitvow/policy.json`, `~/.gitvow/git-hooks/`, `~/.gitvow/ledger/<session>.json`,
`~/.gitvow/cache/`, `~/.gitvow/claims/` (your own preferences), and the user-scope agent settings files.

### Caches

The pack and the brief under `~/.gitvow/cache/` are copies of what a store published, keyed by the
repository's source. Neither is derived from your policy or from gitvow's version, so a policy change applies
on the next tool call with nothing to invalidate: the pack is validated and applied at load, every time, and a
pack of an unknown schema, or past its `expires`, changes nothing and says why. The brief carries the store's
`stale_after` and is marked stale past it. There is no other cache.

## What it executes

- `git`, as a subprocess, for the commands named above. Never with `-c core.hooksPath` or `--no-verify`.
- `python3`, from the git hooks, to read the session state; the hooks are shell scripts that chain to the
  repository's own hooks when present.
- A provider or external adapter you configured, as a subprocess, with the hook payload on stdin.
- Nothing from the repository. gitvow never runs a build, a test, or a command suggested by repository
  content. `gitvow check -- <command>` evaluates a command against policy and does not run it.

## What it sends

Once you push, two things go to your remote, because they are inside git: commit trailers, and session notes
under `refs/notes/gitvow/*`. `gitvow sync` sends an [export bundle](reference/export.md) to the sinks you
configured, one file per consented data class, with a manifest that lists what was refused and what never
travels; the bundle is readable before it moves and contains no transcript text, no working-tree content, no
snapshot, no command line and no prompt. Everything else named on this page stays on the machine.

## Determinism and what the gate cannot see

The same policy and the same tool call give the same verdict. The gate inspects the text of a tool call; a
process that a command spawns is not seen separately, and a path check uses the path, not the diff. Regular
expressions are a floor; providers and the classifier raise it, and both are yours to run.

## Reversals

Decisions this tool has reversed or corrected, so that the current behaviour is read with its history:

- **0.15.1, 0.15.2**: `status` stopped repeating install advice and started testing for the consequence, an
  agent-signed commit after the install with no session; then the one-second granularity of `git log --since`
  was corrected, because CI blamed the install for a commit made in the same second.
- **0.17.1**: command rules gained `program` and `verbs` after a replay of 15,549 real tool calls found every
  `kubectl --context … delete` walking past a regex that only matched the verb directly after the program.
- **0.25.0**: the repository a hook acts on is resolved from the tool call, not the session's working
  directory, after our own repositories were found to carry no decisions for that reason.
- **0.26.0**: repository writes are preflighted and skipped when unchanged; credential-store paths are excluded
  by path; a policy can extend the snapshot exclusion list and no longer replace it.
- **0.26.1**: `{program, verbs}` rules match the program in command position only, after an issue whose body
  described a runbook was stopped as a cluster mutation. A quoted argument, a commit message and a heredoc body
  are text, not commands; `sh -c`, `sudo`, `env`, `timeout` and `xargs` still reach the program behind them.
- **0.27.0**: rules may name what a command reaches (`target`), read from the environment; the default policy
  asks for production contexts and observes the rest. Commands whose program the gate cannot read (`eval`, a
  variable, a script run through a shell) stopped falling through to allow and became an observe tier. Both
  follow from the question "isn't regex too risky": as the only barrier it is; as the first tier of a recorder
  that reads the environment and records what it cannot read, it is a floor with a named ceiling.
