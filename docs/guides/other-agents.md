# Use gitvow with Codex CLI, Gemini CLI and Cursor

gitvow's record and gate are agent-neutral: trailers, notes, snapshots and the ledger live in git and in your home directory, and the policy is the same file. What differs per agent is the hook contract, so gitvow ships one adapter per agent that translates each agent's payload into the shape the hooks understand and answers in the form the agent expects.

| Agent | Hooks configured in | Events used | How a block is delivered |
|---|---|---|---|
| Claude Code | `~/.claude/settings.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| Codex CLI | `~/.codex/hooks.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| Gemini CLI | `~/.gemini/settings.json` | SessionStart, BeforeTool, AfterTool, SessionEnd | exit 2, reason on stderr |
| Cursor | `~/.cursor/hooks.json` | sessionStart, preToolUse, beforeShellExecution, afterFileEdit, stop | JSON `permission: deny` or `ask` with `agent_message` |
| Copilot CLI | `~/.copilot/hooks/gitvow.json` | sessionStart, preToolUse, postToolUse, sessionEnd | JSON `permissionDecision: deny` or `ask` with `permissionDecisionReason` |
| Factory Droid | `~/.factory/hooks.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |

## Install

```sh
pip install gitvow
gitvow install --user            # configures every agent found on this machine
gitvow install --user --check    # the same, then runs the self-check so you see the gate work
```

`install` looks for each agent's configuration directory and command, prints what it found, and configures all of them. Pass `--agent codex` (or gemini, cursor, copilot, factory, or an external adapter name) to pick one. `uninstall` with no `--agent` removes gitvow from every agent it configured, leaving any hooks of your own in place.

Each install merges gitvow's entries into that agent's configuration file, idempotently, and `gitvow uninstall --user --agent <name>` removes exactly those entries.

Then prove it is live:

```sh
gitvow status
```

It checks that the hook command the agent will run actually resolves, that the policy and redaction rules load, that the git hooks are in place, and it repeats whatever each agent still needs by hand. Exit code 1 means something is failing. Run it inside a repository.

### Two extra steps for Codex CLI

Both were found in a real session; without them the gate is installed but does nothing.

1. **Trust the hooks.** Codex lists newly added hooks and skips them until a person approves, so installing gitvow is not enough. Start Codex, run `/hooks`, review the four gitvow entries and trust them. Trust is remembered by hash, so changing the hook command means trusting it again. Until then Codex runs your tools with no gate and says nothing. Automation that has already vetted the hooks can pass `--dangerously-bypass-hook-trust` instead. Hooks are on by default; `[features] hooks = false` in `~/.codex/config.toml` turns them off entirely.
2. **Let the agent write to `.git`.** Codex's `workspace-write` sandbox refuses writes inside `.git`, so `git commit` fails with `Unable to create '.git/index.lock'` and no commit, trailer or note is ever produced. Add the repository's git directory to the writable roots:

```toml
# ~/.codex/config.toml
[sandbox_workspace_write]
writable_roots = ["/absolute/path/to/repo/.git"]
```

Check both at once: `gitvow selftest --agent codex` proves the adapter, then in Codex ask for a trivial commit and confirm `git log -1` carries `Gitvow-Session`.

## What is the same
- The gate: the same policy, the same deny, confirm and allow decisions, the same providers and classifier. A confirmation in Cursor is delivered as its native `ask`, so the user sees the question in Cursor's own prompt.
- Trailers on the agent's own commits, session notes with attribution, snapshots after every edit, the ledger at the end of a session.
- `gitvow show`, `gitvow report`, `gitvow snapshots` and the pull request action work unchanged, because they read git, not the agent.

## What differs, honestly
- **Codex edits arrive as patches.** Codex's file edits are a single `apply_patch` call carrying a patch, not a file path. gitvow parses the patch for the files it touches and the lines it adds, so path rules, route questions, attribution and snapshots work, but a rule that depends on the exact edit text sees the patch text.
- **Codex wraps its tool calls in JavaScript.** In the session file, Codex 0.15 records one `exec` tool whose input is a snippet such as `await tools.exec_command({"cmd": ...})` or `await tools.apply_patch("...")`. gitvow unwraps it, so notes and reports name `Bash` and `Edit` rather than `exec`. The hook payload itself is unaffected; this only concerns what the transcript reader can see.
- **Cursor's transcript is not read.** The stated plan and tool counts come from the transcript. gitvow reads Claude Code's format, Codex's session files (`~/.codex/sessions/.../rollout-*.jsonl`) and Gemini CLI's chat recordings (`~/.gemini/tmp/<project>/chats/session-*.jsonl`), each detected from the file itself. Cursor does not document the file behind `transcript_path`, so for Cursor the note records the commit, trailers, snapshot and attribution and leaves the plan empty. The Codex and Gemini readers follow the formats as published in each project's source and community write-ups; they are marked *unverified against a live file* until a user confirms.
- **Cursor has no hook before a file edit** other than the generic `preToolUse`; gitvow uses that for gating and `afterFileEdit` for snapshots. If Cursor's built-in edit tool bypasses `preToolUse` in a future version, the gate still sees shell commands and MCP calls through `beforeShellExecution` and `beforeMCPExecution`.
- **Copilot CLI passes no transcript path**, so its notes never carry a plan; everything else works. Factory passes one, but its format is not documented, so the same applies.
- **Cursor writes no transcript, and its hooks are fail-closed.** The note for a Cursor commit carries the trailers, decisions, attribution and snapshot, and leaves the plan, tool counts and cost empty, because those come from a transcript Cursor does not expose. gitvow installs its Cursor hooks with `failClosed: true`, so every hook answers with a JSON permission object even for events it does not use; a silent hook would block the tool.
- **Verification status.** Claude Code, Codex CLI and Cursor have been exercised in real sessions end to end: the gate collects a finding, the card refuses the commit, a person's answer becomes trailers, and the note carries the decision and attribution. The Gemini CLI, Copilot CLI and Factory adapters are built and tested against the payload shapes in each vendor's documentation, and marked *unverified in a real session* until someone with that agent installed runs `gitvow selftest --agent <name>` and a real session against a scratch repository. Please report what you see.

## An agent not listed here
Write a `gitvow-agent-<name>` executable and put it on the PATH; gitvow discovers it and every command that takes `--agent` accepts the new name. The contract is three small subcommands over JSON, described in the [external adapter protocol](../reference/adapter-protocol.md), with a complete example in the repository.

## Tool name mapping

| Agent tool | gitvow sees |
|---|---|
| Codex `Bash`, Cursor `beforeShellExecution`, Gemini `run_shell_command` | `Bash` with `command` |
| Codex `apply_patch` | one `Edit` per touched file with `file_path`, `old_string` (removed lines), `new_string` (added lines) |
| Gemini `write_file` | `Write` with `file_path`, `content` |
| Gemini `replace` | `Edit` with `file_path`, `old_string`, `new_string` |
| Cursor `afterFileEdit` | `Edit` with `file_path` and the concatenated `edits` |
| Cursor `Write` (its name for any agent file modification) | `Write` with `file_path`; Cursor's `new_content` is also exposed as `content`, so route questions and providers see the new text |
| Cursor `Delete` | `Edit` with `file_path`, so deleting a gate-bearing file is gated like editing one |
| Cursor `MCP:<tool>` with `mcp_server_name` | `mcp__<server>__<tool>` |
| Copilot `bash` / `powershell` | `Bash` with `command` |
| Copilot `edit`, `str_replace_editor`, `apply_patch` / `create` | `Edit` / `Write` with `file_path` (from `path` or `file_path`), `old_string`, `new_string`, `content` |
| Factory `Execute` | `Bash` with `command` |
| Factory `Edit`, `ApplyPatch` / `Create` | `Edit` / `Write` with `file_path`, `old_string`, `new_string`, `content` |
| any `mcp__server__tool` | unchanged |
