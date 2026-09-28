# Hook payloads

Claude Code invokes each hook command with a JSON object on stdin. gitvow reads these fields:

| Field | Used by | Meaning |
|---|---|---|
| `session_id` | all | the session identifier |
| `transcript_path` | PostToolUse, Stop | path to the JSONL transcript Claude Code keeps locally |
| `cwd` | all | the repository being worked on |
| `hook_event_name` | all | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop` |
| `tool_name` | PreToolUse, PostToolUse | e.g. `Bash`, `Edit`, `mcp__server__tool` |
| `tool_input` | PreToolUse, PostToolUse | e.g. `{"command": "..."}` or `{"file_path": "..."}` |
| `prompt` | UserPromptSubmit | the person's message; only the first line of the session's first message is kept, redacted, as the [intent](../guides/intent.md); the message itself is never stored |
| `permission_mode` | PreToolUse | Claude Code's mode; in the prompting modes a confirm is answered as `permissionDecision: ask` |

PostToolUse on an editing tool records the blob id of the written file for attribution and takes a working-tree snapshot; on a `git commit` it writes the session note. UserPromptSubmit records the intent once per session and prints nothing.

Output contract: exit `0` allows the call; exit `2` blocks it and Claude Code feeds stderr back to the model as the reason. gitvow never writes to stdout from a hook.

## Settings entries
`gitvow install` merges these into `settings.json`:

```json
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "gitvow hook SessionStart"}]}],
  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "gitvow hook UserPromptSubmit"}]}],
  "PreToolUse":   [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|mcp__.*", "hooks": [{"type": "command", "command": "gitvow hook PreToolUse"}]}],
  "PostToolUse":  [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit", "hooks": [{"type": "command", "command": "gitvow hook PostToolUse"}]}],
  "Stop":         [{"hooks": [{"type": "command", "command": "gitvow hook Stop"}]}]
}}
```

Entries are recognised by the `gitvow hook ` prefix, so install is idempotent and uninstall removes only these.

## Other agents
Codex CLI, Gemini CLI and Cursor are supported through adapters that translate their payloads into this shape; see [Adapter reference](adapters.md).
