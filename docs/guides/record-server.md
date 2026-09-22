# Let any agent ask the record: the record server

Hooks reach the agents that run tool calls on this machine. Everything else, a review bot, a planning agent, a
tool inside a ticket system, an agent from a vendor that has no hook mechanism, never sees the record. The record
server closes that gap: `gitvow serve` answers questions about one repository's record over MCP, the protocol
every agent speaks, and it is **read-only**. Nothing writes through it: no state, no log line, no note, no network.

```console
$ gitvow serve --print-config
Claude Code:
  claude mcp add gitvow -- gitvow serve --repo /path/to/repo
…
```

Point an agent at it once; from then on its tools are the record's questions.

## The tools

| tool | answers | reads |
|---|---|---|
| `record_brief` | What does the record say about this repository before I touch it? Earned rules, proposals that are not rules, the store's brief with its age when one is cached. | decisions in history, `~/.gitvow/cache/brief/` |
| `record_claims` | What did the owners confirm about this code? Optionally only claims touching a path prefix. | claim trailers in history |
| `record_standing` | How does this class of finding stand: earned rules, proposals, and the decisions people recorded, newest first? | trailers and notes |
| `record_why` | Why does this file look the way it does? | trailers, notes, snapshots |
| `record_recall` | Has this been worked on before? | notes, the ledger |
| `record_handoff` | What should the next agent know to continue? | ledger, notes, uncommitted state |
| `record_pack` | Which organisation rules are in force here, accepted by whom, until when? | `~/.gitvow/cache/packs/` |
| `record_check` | What would the gate say to this command or this edit? A dry run; nothing runs, nothing is recorded. | the policy |
| `record_status` | Is the record being written here? | the install, the session state |

Every tool takes an optional `repo` (a path; the server's own repository by default).

## Two rules every answer obeys

**Coverage is stated on every answer.** Each result carries `coverage`: how many decisions, claims and session
notes exist here, where the brief came from and how old it is, whether the pack applied, and a level:

- `full`: there is a record and nothing it rests on is stale.
- `partial`: the brief is past the store's `stale_after`, or the pack expired or failed to load.
- `none`: no decisions, no claims, no notes, or the policy does not load.

A consumer should treat `partial` and `none` as *absent*, never as a clean bill: a repository with no record is
one nobody has decided anything about yet, which is different from one where everything was allowed.

**Quoted content is data.** Claims and stated plans are what people typed. The server renders them inside a
block that opens with a sentence saying so, and `structuredContent` carries them as fields, never as prose the
reading agent could mistake for its instructions. Prefer the structured form when you parse.

## What it does not do

It does not write. `gitvow decide`, `gitvow claims confirm`, `gitvow rules accept` are things a person does at a
terminal, and they stay there; an agent that could record a decision over MCP would be recording its own
permission. It does not reach other repositories unless asked with `repo`, and then only ones on this machine. It
does not run anything: `record_check` evaluates the policy and stops. It makes no network call and is only
reachable over its own stdin and stdout.

The organisation-wide counterpart, the store's HTTP server, lives on the platform team's side and serves the same
objects across repositories. This one is the single-repository, on-the-machine answer, and it is what the runtime
and deployment observations will be read through once they exist.
