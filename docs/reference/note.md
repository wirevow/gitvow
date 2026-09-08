# Session note schema

Stored under `refs/notes/sessions`. First line `gitvow-session`, then JSON:

| Field | Type | Meaning |
|---|---|---|
| `session_id` | string | Claude Code session id |
| `step` | int | commit number within the session |
| `committed_at` | string | local time the note was written |
| `assistant_turns_so_far` | int | assistant messages in the transcript at commit time |
| `tool_calls_so_far` | int | tool invocations at commit time |
| `tools_used` | string[] | distinct tool names |
| `last_stated_plan` | string | last assistant text before the commit, redacted, at most 600 characters |
| `files_in_commit` | string[] | `git show --stat` lines, at most 50 |
| `files_written_by_agent_this_session` | string[] | files in the commit that the agent edited or wrote via tools in this session |
| `attribution` | object | `files_in_commit`, `touched_by_agent` counts |
| `transcript` | string | always "kept local; see ledger" |
| `redaction` | string | statement of what redaction ran |

The schema is additive. New fields may appear; existing fields keep their meaning. Consumers should ignore unknown fields.

## Trailers
```
Gitvow-Session: <session id>
Gitvow-Step: <int>
```
Added by `prepare-commit-msg` when `.git/gitvow-session.json` exists with a session id. Idempotent: a message that already has `Gitvow-Session:` is left alone.
