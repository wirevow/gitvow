# gitvow

Provenance and a policy gate for AI-agent coding sessions, stored in the git you already have.

[![ci](https://github.com/wirevow/gitvow/actions/workflows/ci.yml/badge.svg)](https://github.com/wirevow/gitvow/actions/workflows/ci.yml)
[![codeql](https://github.com/wirevow/gitvow/actions/workflows/codeql.yml/badge.svg)](https://github.com/wirevow/gitvow/actions/workflows/codeql.yml)
![python](https://img.shields.io/badge/python-3.9%E2%80%933.12-blue) ![deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen) ![license](https://img.shields.io/badge/license-Apache--2.0-blue)

**Who made this change, what were they trying to do, and was it allowed?** For code written with AI agents, git alone cannot answer. gitvow makes it answer.

- Commits made during an agent session carry the session id and a step number as **trailers**.
- Each such commit gets a **session note**: the agent's stated plan, the tools it used, the files it touched, how much of the commit it wrote. Redacted, stored as a git note, never in the tree.
- Every tool call passes a **gate** first: destructive commands are refused, risky ones require asking you, edits to gate-bearing files need a human. The rules are a JSON file you own. Missing policy fails closed.
- A **ledger** of the whole session stays in your home directory. Nothing leaves the machine unless you push it.

Standard-library Python and git. No runtime dependencies, no network calls, no telemetry.

## Quick start

```sh
pip install gitvow
gitvow install --user
gitvow selftest
```

Work in Claude Code as usual. When the agent commits:

```sh
$ git log -1 --format=%B
Fix week-start cache key

Gitvow-Session: 8f3d5c71-574a-4eec-8903-9425e3a8335b
Gitvow-Step: 4

$ gitvow show HEAD
...
gitvow-session
{
 "step": 4,
 "tools_used": ["Bash", "Edit", "Read"],
 "last_stated_plan": "Change the cache key to include week start so per-org settings do not collide...",
 "files_in_commit": ["query-engine/.../QueryCacheHelper.java | 4 +++-"],
 "files_written_by_agent_this_session": ["query-engine/.../QueryCacheHelper.java"],
 "attribution": {"files_in_commit": 1, "touched_by_agent": 1}
}
```

Try the gate by hand:

```sh
gitvow check -- git push --force          # DENY: force push
gitvow check -- kubectl apply -f x.yaml   # CONFIRM: cluster apply
gitvow check --path core/authz_rules.go   # CONFIRM: edits an authorization or gate file
```

Remove everything:

```sh
gitvow uninstall --user
```

## Documentation

The docs site is the source of truth: **https://wirevow.dev/gitvow** (built from `docs/`).

- [Quick start](docs/quickstart.md)
- Concepts: [Sessions, steps and notes](docs/concepts/sessions.md) · [The gate](docs/concepts/gate.md) · [What stays out of git](docs/concepts/storage.md)
- Guides: [Install per user or per repo](docs/guides/install.md) · [Write a policy](docs/guides/policy.md) · [Read a commit's session](docs/guides/reading.md) · [Run a trial](docs/guides/trial.md) · [Redaction](docs/guides/redaction.md)
- Reference: [CLI](docs/reference/cli.md) · [Hook payloads](docs/reference/hooks.md) · [Note schema](docs/reference/note.md) · [Policy schema](docs/reference/policy.md)
- [Security](docs/security.md) · [Roadmap](docs/roadmap.md) · [FAQ](docs/faq.md)

## How it works

```
Claude Code ──hook──▶ gitvow hook PreToolUse ──▶ policy ──▶ allow / confirm / deny  (exit 0 / 2 / 2)
            ──hook──▶ gitvow hook PostToolUse ─▶ on `git commit`: read transcript → redact → git notes add
git commit ──prepare-commit-msg──▶ Gitvow-Session / Gitvow-Step trailers   (from .git/gitvow-session.json)
Claude Code ──hook──▶ gitvow hook Stop ─────────▶ ~/.gitvow/ledger/<session>.json
```

| Data | Where | Enters git? |
|---|---|---|
| session id, step | commit trailers | yes |
| session note (structure, redacted plan, attribution) | `refs/notes/sessions` | as a note; local until pushed |
| ledger, hook log, session state | `~/.gitvow/`, `<repo>/.git/` | no |
| transcript | untouched | never |

## Status

0.1.0. Used in a small internal trial; the [roadmap](docs/roadmap.md) lists what comes next and what is deliberately not planned. Claude Code is the only agent supported today; the hook payload is [documented](docs/reference/hooks.md) so adapters are straightforward.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). Reproductions of redaction gaps must use synthetic secrets.

Apache-2.0.
