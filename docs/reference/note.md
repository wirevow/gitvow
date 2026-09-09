# Session note schema

Stored under `refs/notes/gitvow/<session-id>` (gitvow 0.1 used the single ref `refs/notes/sessions`). First line `gitvow-session`, then JSON:

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Claude Code session id |
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
| `schema` | int | note schema version, currently 5 |
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
| `answer` | string | `accepted`, `declined` or `open` |
| `by` | string or null | committer identity used in the trailer: email local part or name |
| `authority` | string | `policy` (named in `decisions.authorities`), `commit-access` (no list configured), `none` |
| `scope` | string or null | environment or branch the answer is limited to |
| `note` | string or null | the person's reason, redacted |
| `decided_at` | string or null | local time the answer was recorded |
| `human_turns_after_card` | int or null | user messages in the transcript between the card and the answer; `0` means the agent answered without a person speaking |

The schema is additive. New fields may appear; existing fields keep their meaning. Consumers should ignore unknown fields.

## Trailers
```
Gitvow-Session: <session id>
Gitvow-Step: <int>
Gitvow-Accepted: <finding> by <person>[ scope=<scope>][: <reason>]
Gitvow-Declined: <finding> by <person>[ scope=<scope>][: <reason>]
Gitvow-Open: <finding>
```
Added by `prepare-commit-msg` when `.git/gitvow-session.json` holds a session id and a `pending_commit` timestamp younger than five minutes, which the PreToolUse gate sets when the agent runs `git commit` and PostToolUse clears afterwards. Idempotent: a message that already has `Gitvow-Session:` is left alone. A note is written only when the commit at HEAD carries the trailer.

`Gitvow-Accepted` and `Gitvow-Declined` are written for every finding decided with `gitvow decide` before the commit, on agent and human commits alike. `Gitvow-Open` is written on a human commit for each finding nobody decided (policy `decisions.mode` `open`). The `post-commit` hook clears the findings the commit carried. See [Decisions](../concepts/decisions.md).
