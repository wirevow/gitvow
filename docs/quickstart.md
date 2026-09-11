# Quick start

Requirements: git, Python 3.9 or newer, and at least one coding agent. Claude Code, Codex CLI and Cursor are verified in real sessions; Gemini CLI, Copilot CLI and Factory have adapters awaiting a first real session. See [Use gitvow with other agents](guides/other-agents.md).

## Look first

```sh
pipx run gitvow scan
```

Reads `git log` in any repository and tells you how much of the recent history an agent wrote, how much of that touched a file your policy calls consequential, and how much of it records who agreed. Nothing is installed and nothing is written. See [Look before installing](guides/scan.md).

## Install for yourself

```sh
pip install gitvow
gitvow install --user --check
```

`install` looks for every agent on this machine and configures all of them, printing what it found. It also writes a default policy to `~/.gitvow/policy.json` and four git hooks to `~/.gitvow/git-hooks`, registered as your global hooks path, so every repository you open is covered. `--check` runs the self-check straight afterwards so you see the gate work before trusting it with real code.

To configure one agent only, pass `--agent codex` (or `gemini`, `cursor`, `copilot`, `factory`, or the name of an [external adapter](reference/adapter-protocol.md)).

**Codex CLI needs two more steps** that no installer can do for you, and without them the gate is installed but inert. `install` prints them, and [the agents guide](guides/other-agents.md) explains them.

## Is it actually working?

```sh
gitvow status
```

Run it inside a repository. It checks that the hook command each agent will run actually resolves, that the policy and redaction rules load, that all four git hooks are present, and whether any session has been recorded here yet. Every failure prints the line that fixes it, and it exits non-zero when something is wrong.

```
  ok   policy loads: 16 rules, 0 providers
  ok   redaction rules load
  ok   claude (user): 4 hooks in ~/.claude/settings.json → /usr/local/bin/gitvow
  ok   codex (user): 4 hooks in ~/.codex/hooks.json → /usr/local/bin/gitvow
  note codex: hooks are skipped until trusted
         Start codex, run `/hooks`, review the gitvow entries and trust them.
  ok   git hooks in ~/.gitvow/git-hooks: prepare-commit-msg, pre-push, pre-commit, post-commit
  ok   notes.displayRef / rewriteRef → refs/notes/gitvow/*
  note no hook log in this repository yet, so no session has been recorded here

Ready: run a session and commit, or `gitvow selftest` to see it work now. 1 step left to you above.
```

The self-check is the other half: it creates a throwaway repository, drives every hook with the payloads your agent sends, and touches nothing you own.

```
  ok   session recorded in .git
  ok   deny: force push blocked
  ok   confirm: git push requires asking
  ok   allow: harmless command
  ok   at commit: gate-bearing file edit recorded as a finding
  ok   deny: destructive MCP tool
  ok   at commit: provider says new route is unauthorised, recorded
  ok   snapshot taken after the agent edit
  ok   snapshot holds the agent's version of the file
  ok   card: commit waits for decisions
  ok   card: commit proceeds once every finding is decided
  ok   commit trailer added
  ok   decision trailers added
  ok   session note attached on the session's own ref
  ok   note carries the decisions; open list cleared
  ok   line attribution
  ok   note follows amend
  ok   referral trailer added, apart from accept and decline
  ok   ledger written

selftest: 19 passed, 0 failed
```

`gitvow selftest --agent codex` drives another agent's payload shapes instead.

## Work normally

Start a session in any repository and let the agent work. Two things will happen that did not before.

**Destructive commands are refused outright**, with the reason. Risky ones are put to you first.

**Consequential edits wait for the commit.** When the agent runs `git commit`, the commit is refused once and the agent shows you a card: each finding, its evidence, and what this repository decided last time. You answer in the conversation, the agent records it, and the commit goes through carrying your answer:

```sh
git log -1 --format=%B
```

```
Expose orders export

Gitvow-Session: 7f441e60-f2ef-439c-abaa-1e27028cc793
Gitvow-Step: 1
Gitvow-Accepted: edit core/authz_rules.go by nikhil scope=staging: reviewed with security
```

Then `gitvow show HEAD` adds the session note behind it: the plan, the tools, the attribution, the cost and the decision with its evidence. See [Answer the card](guides/decisions.md).

## Try the gate by hand

```sh
gitvow check -- git push --force      # DENY: force push
gitvow check -- git push origin main  # CONFIRM: pushing to a remote
gitvow check --path core/authz.go     # CONFIRM AT COMMIT: asked on the card instead
gitvow decisions                      # the card for this repository, if anything is open
```

## Remove it

```sh
gitvow uninstall --user
```

With no `--agent`, this removes gitvow from every agent it configured, leaving any hooks of your own in place. Trailers already in commit history stay, because they are part of the commits.
