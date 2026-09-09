# Adapter reference

`gitvow hook --agent <claude|codex|gemini|cursor|copilot|factory|any external name> <Event>` reads the agent's payload on stdin, normalises it, runs the corresponding gitvow handler, and answers in the agent's form. `--agent claude` is the default and changes nothing.

## Normalised payload
Every adapter produces the Claude Code shape documented in [Hook payloads](hooks.md): `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `tool_name`, `tool_input`. Session ids come from `session_id` (Codex, Gemini, Factory), `conversation_id` (Cursor) or `sessionId` (Copilot, whose payloads are camelCase: `toolName`, `toolArgs`).

## Event mapping

| gitvow handler | Codex | Gemini | Cursor | Copilot CLI | Factory |
|---|---|---|---|---|---|
| SessionStart | `SessionStart` | `SessionStart` | `sessionStart` | `sessionStart` | `SessionStart` |
| PreToolUse | `PreToolUse` | `BeforeTool` | `preToolUse`, `beforeShellExecution`, `beforeMCPExecution` | `preToolUse` | `PreToolUse` |
| PostToolUse | `PostToolUse` | `AfterTool` | `afterFileEdit`, `afterShellExecution` | `postToolUse` | `PostToolUse` |
| Stop | `Stop` | `SessionEnd` | `stop` | `sessionEnd` | `Stop` |

## Responses

- **Codex, Gemini**: allow is exit 0 with no stdout; deny and confirm are exit 2 with the message on stderr. Both agents document exit 2 as a block with stderr as the reason.
- **Copilot CLI**: stdout JSON `{"permissionDecision": "allow" | "deny" | "ask", "permissionDecisionReason": ...}`, exit 0. Copilot treats exit 2 as deny too, but JSON is what lets a confirmation become its native `ask`.
- **Factory**: exit 2 with the message on stderr, as documented for `PreToolUse`.
- **Cursor**: stdout JSON. Allow is `{"permission": "allow"}`. Deny is `{"permission": "deny", "user_message": ..., "agent_message": ...}`. Confirm is `{"permission": "ask", ...}`, so Cursor prompts the user natively. Exit code 0 in all cases.

## Codex patch parsing
`apply_patch` input is a text in the `*** Begin Patch` format. gitvow extracts each `*** Update File: <path>`, `*** Add File: <path>` and `*** Delete File: <path>` block; added lines (`+`) become `new_string` and removed lines (`-`) become `old_string` for that file. Route literals and path rules are evaluated per file; a deny or confirm on any file blocks the whole patch, with the file named in the message.

## Install locations

| Agent | File | Notes |
|---|---|---|
| Codex | `~/.codex/hooks.json` | `hooks` must be enabled in `~/.codex/config.toml` (`[features] hooks = true` on older versions) |
| Gemini | `~/.gemini/settings.json` under `hooks` | Gemini requires a `name` per hook entry; gitvow uses `gitvow-<event>` |
| Cursor | `~/.cursor/hooks.json` | `version: 1`; `failClosed: true` is set so a missing `gitvow` blocks rather than passes |
| Copilot CLI | `~/.copilot/hooks/gitvow.json` | `version: 1`; entries use `type: command` with `bash`; Copilot fails closed on non-zero exits for `preToolUse` |
| Factory | `~/.factory/hooks.json` | same event schema as Claude Code |

Per-repo installs (`gitvow install <repo> --agent ...`) write the project-level equivalents: `.codex/hooks.json`, `.gemini/settings.json`, `.cursor/hooks.json`, `.github/hooks/gitvow.json`, `.factory/hooks.json`.
