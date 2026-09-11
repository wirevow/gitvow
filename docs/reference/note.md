# Session note schema

Stored under `refs/notes/gitvow/<session-id>` (gitvow 0.1 used the single ref `refs/notes/sessions`). First line `gitvow-session`, then JSON:

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | the session id the agent assigns to the conversation |
| `step` | int | commit number within the session |
| `committed_at` | string | local time the note was written |
| `assistant_turns_so_far` | int | assistant messages in the transcript at commit time |
| `tool_calls_so_far` | int | tool invocations at commit time |
| `tools_used` | string[] | distinct tool names |
| `last_stated_plan` | string | last assistant text before the commit, redacted, at most 600 characters; read from Claude Code, Codex and Gemini CLI transcripts, empty for Cursor |
| `files_in_commit` | string[] | `git show --stat` lines, at most 50 |
| `files_written_by_agent_this_session` | string[] | files in the commit that the agent edited or wrote via tools in this session |
| `attribution` | object | see below |
| `usage` | object | tokens by kind, models seen, `estimated_cost_usd`, pricing table used; see [What each session cost](../guides/cost.md) |
| `subagents` | object | `count` and `tool_calls` of side conversations in the session |
| `snapshot` | string or null | the last snapshot ref taken before this commit, e.g. `refs/gitvow/snapshots/<session>/7` |
| `decisions` | object[] | the findings this commit carried and how they were answered; see below |
| `schema` | int | note schema version, currently 6 |
| `transcript` | string | always "kept local; see ledger" |
| `redaction` | string | statement of what redaction ran |

### `attribution`

| Field | Type | Meaning |
|---|---|---|
| `files_in_commit` | int | files changed by the commit |
| `touched_by_agent` | int | of those, files the agent wrote or edited this session |
| `lines_added_in_commit` | int | sum of added lines over all files |
| `lines_changed_by_human_after_agent` | int | lines added or removed by a person after the agent's last write, over agent-touched files |
| `agent_share` | float or null | share of added lines that match the agent's version; null when the commit adds no lines or no agent-written blob was recorded |
| `files[]` | object[] | per file: `path`, `agent_wrote`, `lines_added_in_commit`, `human_lines_added`, `human_lines_removed`, `agent_blob`, `committed_blob` |

### `decisions[]`

| Field | Type | Meaning |
|---|---|---|
| `finding` | string | the finding as it appears in the trailer, e.g. `route /v1/x in src/api.py` |
| `kind` | string | `edit`, `route`, `route-removal`, `gate-file` |
| `subject` | string | the path or route |
| `path` | string | the file the finding came from |
| `reason` | string | the rule's reason or the provider's summary |
| `evidence` | string[] | provider evidence, redacted, at most 8 lines |
| `raised` | int | how many tool calls raised the same finding in the session |
| `answer` | string | `accepted`, `declined`, `referred` or `open` |
| `by` | string or null | committer identity used in the trailer: email local part or name |
| `authority` | string | `policy` (named in `decisions.authorities`), `commit-access` (no list configured), `none` |
| `scope` | string or null | environment or branch the answer is limited to. A scoped answer is an exception, so it never counts towards a proposed rule |
| `to` | string or null | with `answer: referred`, who the question should go to; null when the person did not name anyone (schema 6) |
| `note` | string or null | the person's reason, redacted |
| `decided_at` | string or null | local time the answer was recorded |
| `human_turns_after_card` | int or null | user messages in the transcript between the card and the answer; `0` means the agent answered without a person speaking |
| `proposed` | string or null | what the record proposed on the card from earlier decisions on the same finding: `accept`, `decline` or null |

The schema is additive. New fields may appear; existing fields keep their meaning. Consumers should ignore unknown fields. Schema 6 added `to` to `decisions[]`; a note written by an older gitvow reads the same as it always did.

## The rule-decision note

Accepting or rejecting a proposed rule is itself a decision, so it is recorded the way decisions are. `gitvow rules accept|reject` writes an empty commit carrying the trailer and attaches a note under `refs/notes/gitvow/rules` — its own ref, because a rule decision is not a session. First line `gitvow-rule-decision`, then JSON:

| Field | Type | Meaning |
|---|---|---|
| `schema` | int | rule-decision note schema, currently 1 |
| `verdict` | string | `accepted` or `rejected` |
| `by` | string | the authority who decided |
| `authority` | string | `policy` or `commit-access`; never `none`, because a verdict by an unnamed person is refused rather than recorded |
| `reason` | string or null | the person's reason, redacted |
| `decided_at` | string | local time the verdict was recorded |
| `threshold` | int | `decisions.rule_threshold` in force when the verdict was made |
| `rules[]` | object[] | one per proposal in this verdict: `finding`, `kind`, `answer`, `count`, `first`, `last`, `by[]`, `evidence[]`, `exceptions`, `exception_scopes[]`, `expires` |

`rules[].evidence[]` is the run the proposal rested on, oldest first, at most 8 entries of `date`, `by`, `answer` and `sha`. It is stored at the moment of the verdict so the record says not only that a rule was accepted but what was in front of the person who accepted it.

## Trailers
```
Gitvow-Session: <session id>
Gitvow-Step: <int>
Gitvow-Accepted: <finding> by <person>[ scope=<scope>][: <reason>]
Gitvow-Declined: <finding> by <person>[ scope=<scope>][: <reason>]
Gitvow-Referred: <finding> by <person>[ to=<person-or-team>][: <reason>]
Gitvow-Open: <finding>
Gitvow-Revisits: <commit>
Gitvow-Rule-Accepted: <finding> by <person>[ answer=<accepted|declined>][: <reason>]
Gitvow-Rule-Rejected: <finding> by <person>[ answer=<accepted|declined>][: <reason>]
```
Added by `prepare-commit-msg` when `.git/gitvow-session.json` holds a session id and a `pending_commit` timestamp younger than five minutes, which the PreToolUse gate sets when the agent runs `git commit` and PostToolUse clears afterwards. Idempotent: a message that already has `Gitvow-Session:` is left alone. A note is written only when the commit at HEAD carries the trailer.

`Gitvow-Accepted`, `Gitvow-Declined` and `Gitvow-Referred` are written for every finding decided with `gitvow decide` before the commit, on agent and human commits alike. `Gitvow-Open` is written on a human commit for each finding nobody decided (policy `decisions.mode` `open`). The `post-commit` hook clears the findings the commit carried. `Gitvow-Revisits` marks an empty commit written by `gitvow revisit`; its decision trailers answer the named commit's findings again. `Gitvow-Rule-Accepted` and `Gitvow-Rule-Rejected` mark an empty commit written by `gitvow rules accept|reject`; `answer=` says which run the verdict was about, so a later contradicting decision does not inherit it. See [Decisions](../concepts/decisions.md).

The grammar grows by new trailer **names**, never by new tail tokens on a name that has already shipped. `Gitvow-Referred` and its `to=` arrived together in 0.16: an older gitvow does not match the new name at all, so it reads the commit exactly as it would have before, which is the safe direction. Adding a token to a trailer that already exists is not safe, and the reason is in the pattern above — the finding is matched lazily and every suffix is optional, so an older parser that does not know the token backtracks it *into* the finding. `Gitvow-Accepted: edit foo.go class=k7 by nikhil` would parse on 0.15 as a decision about `edit foo.go class=k7`, silently forking the identity of every decision that carried it, with nothing anywhere reporting an error. Treat the token set of a released trailer as closed: a new fact belongs in a new trailer name, or in the note, which is versioned and genuinely additive. Within that constraint every part after the finding is optional, so a commit written by any earlier gitvow parses exactly as it did when it was made, and no existing trailer name has changed meaning. `Gitvow-Rule-Accepted` is deliberately not a `Gitvow-Accepted`: accepting a rule is not an answer to a finding on that commit, and a parser looking for decisions must not pick it up.
