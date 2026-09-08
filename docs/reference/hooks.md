# Hook payloads

Claude Code invokes each hook command with a JSON object on stdin. gitvow reads these fields:

| Field | Used by | Meaning |
|---|---|---|
| `session_id` | all | the session identifier |
| `transcript_path` | PostToolUse, Stop | path to the JSONL transcript Claude Code keeps locally |
| `cwd` | all | the repository being worked on |
| `hook_event_name` | all | `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop` |
| `tool_name` | PreToolUse, PostToolUse | e.g. `Bash`, `Edit`, `mcp__server__tool` |
| `tool_input` | PreToolUse, PostToolUse | e.g. `{"command": "..."}` or `{"file_path": "..."}` |

Output contract: exit `0` allows the call; exit `2` blocks it and Claude Code feeds stderr back to the model as the reason. gitvow never writes to stdout from a hook.

## Settings entries
`gitvow install` merges these into `settings.json`:

```json
{"hooks": {
  "SessionStart": [{"hooks": [{"type": "command", "command": "gitvow hook SessionStart"}]}],
  "PreToolUse":   [{"matcher": "Bash|Edit|Write|MultiEdit|NotebookEdit|mcp__.*", "hooks": [{"type": "command", "command": "gitvow hook PreToolUse"}]}],
  "PostToolUse":  [{"matcher": "Bash", "hooks": [{"type": "command", "command": "gitvow hook PostToolUse"}]}],
  "Stop":         [{"hooks": [{"type": "command", "command": "gitvow hook Stop"}]}]
}}
```

Entries are recognised by the `gitvow hook ` prefix, so install is idempotent and uninstall removes only these.

## Other agents
Any agent that can run a command before and after tool calls and pass this shape can use gitvow unchanged. Adapters that translate other payload formats are welcome; keep them in `gitvow/adapters/`.
