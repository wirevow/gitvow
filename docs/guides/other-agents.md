# Use gitvow with Codex CLI, Gemini CLI and Cursor

gitvow's record and gate are agent-neutral: trailers, notes, snapshots and the ledger live in git and in your home directory, and the policy is the same file. What differs per agent is the hook contract, so gitvow ships one adapter per agent that translates each agent's payload into the shape the hooks understand and answers in the form the agent expects.

| Agent | Hooks configured in | Events used | How a block is delivered |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| Codex CLI | `~/.codex/hooks.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| Gemini CLI | `~/.gemini/settings.json` | SessionStart, BeforeTool, AfterTool, SessionEnd | exit 2, reason on stderr |
| Cursor | `~/.cursor/hooks.json` | sessionStart, preToolUse, beforeShellExecution, afterFileEdit, stop | JSON `permission: deny` or `ask` with `agent_message` |

## Install

```sh
pip install gitvow
gitvow install --user --agent codex     # or gemini, cursor; repeat per agent you use
gitvow install --user                   # Claude Code, as before
```

Each install merges gitvow's entries into that agent's configuration file, idempotently, and `gitvow uninstall --user --agent <name>` removes exactly those entries. Codex requires hooks to be enabled in `~/.codex/config.toml`; the installer prints the line if it is missing.

## What is the same
- The gate: the same policy, the same deny, confirm and allow decisions, the same providers and classifier. A confirmation in Cursor is delivered as its native `ask`, so the user sees the question in Cursor's own prompt.
- Trailers on the agent's own commits, session notes with attribution, snapshots after every edit, the ledger at the end of a session.
- `gitvow show`, `gitvow report`, `gitvow snapshots` and the pull request action work unchanged, because they read git, not the agent.

## What differs, honestly
- **Codex edits arrive as patches.** Codex's file edits are a single `apply_patch` call carrying a patch, not a file path. gitvow parses the patch for the files it touches and the lines it adds, so path rules, route questions, attribution and snapshots work, but a rule that depends on the exact edit text sees the patch text.
- **Cursor's transcript is not read.** The stated plan and tool counts come from the transcript. gitvow reads Claude Code's format, Codex's session files (`~/.codex/sessions/.../rollout-*.jsonl`) and Gemini CLI's chat recordings (`~/.gemini/tmp/<project>/chats/session-*.jsonl`), each detected from the file itself. Cursor does not document the file behind `transcript_path`, so for Cursor the note records the commit, trailers, snapshot and attribution and leaves the plan empty. The Codex and Gemini readers follow the formats as published in each project's source and community write-ups; they are marked *unverified against a live file* until a user confirms.
- **Cursor has no hook before a file edit** other than the generic `preToolUse`; gitvow uses that for gating and `afterFileEdit` for snapshots. If Cursor's built-in edit tool bypasses `preToolUse` in a future version, the gate still sees shell commands and MCP calls through `beforeShellExecution` and `beforeMCPExecution`.
- **Verification status.** Claude Code's adapter has been exercised in real sessions throughout. The Codex, Gemini and Cursor adapters are built and tested against the payload shapes in each vendor's documentation, and marked *unverified in a real session* until someone with that agent installed runs `gitvow selftest --agent <name>` and a real session against a scratch repository. Please report what you see.

## Tool name mapping

| Agent tool | gitvow sees |
|---|---|
| Codex `Bash`, Cursor `beforeShellExecution`, Gemini `run_shell_command` | `Bash` with `command` |
| Codex `apply_patch` | one `Edit` per touched file with `file_path`, `old_string` (removed lines), `new_string` (added lines) |
| Gemini `write_file` | `Write` with `file_path`, `content` |
| Gemini `replace` | `Edit` with `file_path`, `old_string`, `new_string` |
| Cursor `afterFileEdit` | `Edit` with `file_path` and the concatenated `edits` |
| any `mcp__server__tool` | unchanged |
