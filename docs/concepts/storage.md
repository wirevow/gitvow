# What stays out of git

The agent's transcript contains tool output: file contents, query results, environment variables, and whatever a database returned. It must not be committed to a repository. gitvow is built around that constraint.

| Data | Where it lives | Enters git? |
|---|---|---|
| Session id and step | commit message trailers | yes, by design |
| Session note: structure, tool names, redacted plan, attribution | `refs/notes/gitvow/<session-id>` | yes, as a note, not in the tree; local until pushed |
| Agent-written file versions (blob ids for attribution) | `.git/objects`, unreachable | no, never pushed; pruned by `git gc` |
| Ledger: redacted tool calls, commits, plan | `~/.gitvow/ledger/` | no |
| Hook log: every allow, confirm, deny | `<repo>/.git/gitvow-hooks.log` | no, `.git` is never pushed |
| Session state | `<repo>/.git/gitvow-session.json` | no |
| Transcript | wherever your agent keeps it | never touched |

## Choosing where the full session should live

For a team that wants more than the note, four options, in rising order of ambition:

1. **The developer's machine only.** The default. Nothing leaves.
2. **A restricted ledger repository.** A separate private repository receiving only the redacted ledger JSON per session. You keep history and blame; you never hold raw content.
3. **An encrypted object store with retention.** Raw transcripts under KMS with a write-once retention policy, referenced from the note by session id and hash. For organisations that need full replay for audit. The only option that retains raw content, so treat it as production data.
4. **An engineering record.** Ledger events as facts next to a derived model of the codebase, so "why" is queryable beside "what". Redacted only. Out of gitvow's scope; gitvow is the source such a record consumes.

Do not put transcripts in wikis or tickets. They are treated as documentation, outlive their redaction assumptions, and are searchable by everyone.

## Redaction is a floor
Everything that enters a note, the ledger or the log passes through [redaction](../guides/redaction.md), including any rules you add in `redact-rules.json`. It lowers the probability that a secret reaches git. It cannot make it zero, and it does not protect a repository from the people who can already read it.
