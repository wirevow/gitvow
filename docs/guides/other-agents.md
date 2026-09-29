# Use gitvow with Codex, Cursor and other agents

gitvow's record and gate are agent-neutral: trailers, notes, snapshots and the ledger live in git and in your home directory, and the policy is the same file. What differs per agent is the hook contract, so gitvow ships one adapter per agent that translates each agent's payload into the shape the hooks understand and answers in the form the agent expects.

| Agent | Verified | Hooks configured in | Events used | How a block is delivered |
|---|---|---|---|---|
| Claude Code | real session | `~/.claude/settings.json` | SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr; `permissionDecision: ask` for a confirm in the prompting modes |
| Codex CLI | real session | `~/.codex/hooks.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| Gemini CLI | real session | `~/.gemini/settings.json` | SessionStart, BeforeTool, AfterTool, SessionEnd | exit 2, reason on stderr; the agent sees "Tool execution blocked" with gitvow's reason |
| Cursor | real session | `~/.cursor/hooks.json` | sessionStart, preToolUse, beforeShellExecution, afterFileEdit, stop | JSON `permission: deny` or `ask` with `agent_message` |
| Copilot CLI | vendor docs only | `~/.copilot/hooks/gitvow.json` | sessionStart, preToolUse, postToolUse, sessionEnd | JSON `permissionDecision: deny` or `ask` with `permissionDecisionReason` |
| Factory Droid | vendor docs only | `~/.factory/hooks.json` | SessionStart, PreToolUse, PostToolUse, Stop | exit 2, reason on stderr |
| OpenCode | real session | `~/.config/opencode/plugins/gitvow.js` (a plugin gitvow writes) | session.created, chat.message, tool.execute.before, tool.execute.after, session.idle | the plugin throws with gitvow's reason, which OpenCode shows as the tool's error |

## Install

```sh
pip install gitvow
gitvow install --user            # configures every agent found on this machine
gitvow install --user --check    # the same, then runs the self-check so you see the gate work
```

`install` looks for each agent's configuration directory and command, prints what it found, and configures all of them. Pass `--agent codex` (or gemini, cursor, copilot, factory, opencode, or an external adapter name) to pick one. `uninstall` with no `--agent` removes gitvow from every agent it configured, leaving any hooks of your own in place.

Each install merges gitvow's entries into that agent's configuration file, idempotently, and `gitvow uninstall --user --agent <name>` removes exactly those entries.

Then prove it is live:

```sh
gitvow status
```

It checks that the hook command the agent will run actually resolves, that the policy and redaction rules load, that the git hooks are in place, and it repeats whatever each agent still needs by hand. Exit code 1 means something is failing. Run it inside a repository.

### Restarting after an install

All of these read their hook configuration when a session starts, so a session already running when you install is not gated. Cursor is the exception: it watches its hook file and reloads by itself. `gitvow status` detects the consequence directly, by looking for agent commits made after the install that carry no session.

### One thing to know about desktop agents

Cursor runs as a desktop application, so it does not inherit the PATH of your terminal. A per-repository install records the bare command `gitvow`, which a desktop agent frequently cannot find; the hook then fails, and because gitvow installs Cursor's hooks fail-closed, Cursor blocks the tool and reports that the hook returned no output. Install per user as well, so the absolute path is recorded, and run `gitvow status`, which resolves the exact command each agent will run and says which of them depends on PATH.

### OpenCode loads a plugin, not hooks

OpenCode has no hook commands; it loads JavaScript plugins from `~/.config/opencode/plugins/` (or `.opencode/plugins/` in a repository). `gitvow install --agent opencode` writes one, `gitvow.js`, that runs `gitvow hook --agent opencode <event>` with the payload on stdin for `session.created`, `chat.message`, `tool.execute.before`, `tool.execute.after` and `session.idle`, and **throws** when gitvow answers exit 2. Throwing is how a plugin blocks a tool there, so OpenCode shows gitvow's reason as the tool's error and the agent relays it. A confirm is therefore never OpenCode's own question: the agent asks you, records the answer with `gitvow decide <n> accept --scope session`, and runs the command again. The plugin fails closed: when gitvow cannot be run at all, every tool call is refused with the reason. The first message of a session becomes the intent through `chat.message`, as in Claude Code. OpenCode stores sessions as its own files rather than a transcript gitvow reads, so notes carry no plan, tool counts or cost there, like Cursor. Restart OpenCode after installing; plugins load at start-up. The absolute path of `gitvow` is written into the plugin, so a desktop launch without your shell's PATH still finds it.

### Gemini CLI turns git's hooks off, so gitvow writes the record itself

Found in a real session (Gemini CLI 0.61, 2026-09-29). Gemini runs every shell command, and every hook it calls, with `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1` and `core.hooksPath` overridden to empty, so that a repository's git configuration cannot run commands. That is a sound default for Gemini and it means gitvow's git hooks never run under it: the gate and the card work, because they are Gemini's own hooks, but a commit the agent makes would carry no trailer and get no note, and a push would leave the notes behind.

Since 0.34 the record does not depend on the git hooks. When `AfterTool` sees the agent's commit inside the commit window with no session trailer, gitvow writes the trailers itself, by amending the message of that just-made, unpushed commit (session, step, intent, decisions, open and observed findings, exactly what `prepare-commit-msg` writes), moves the findings as `post-commit` would, attaches the note, and says so in its reply: "trailers written by gitvow because the git hooks did not run", with the reason it can see (the overridden environment when the hook runs inside it, otherwise a pointer to `gitvow status`). When the agent pushes, gitvow pushes the notes to the same remote afterwards, since the `pre-push` hook did not. A person's terminal commit is never touched; the repair applies only to a commit the gate saw the agent make. Identity for `gitvow decide` run inside Gemini's shell is read from `~/.gitconfig` directly, because the hidden global config is where most people keep it.

Two things stay different under Gemini. Strict mode's refusal of a person's commit is a `pre-commit` hook and does not run there; the agent's commit is refused by the card as everywhere. And Gemini's Google login for individuals has been retired in favour of Antigravity, so a verification needs an API key in `~/.gemini/.env`; the session above ran on `gemini-3-flash-preview` with `GEMINI_CLI_TRUST_WORKSPACE=true` for headless use.

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
- The gate: the same policy, the same deny, confirm and allow decisions, the same providers and classifier. A confirmation in Cursor is delivered as its native `ask`, so the user sees the question in Cursor's own prompt; Claude Code gets the same (`permissionDecision: ask`) in the permission modes that show prompts, and a hard block in the ones that do not.
- Trailers on the agent's own commits, session notes with attribution, snapshots after every edit, the ledger at the end of a session.
- `gitvow show`, `gitvow report`, `gitvow snapshots` and the pull request action work unchanged, because they read git, not the agent.

## What differs, honestly
- **Only Claude Code hands gitvow the person's message.** The [intent](intent.md) is taken from the first line of the session's first message through `UserPromptSubmit`, which the other adapters do not have yet. With Codex, Cursor, Gemini, Copilot and Factory the intent is recorded by `gitvow intent "<words>"`, run by the person or by the agent after asking once; the trailer, the note and the card are then identical.
- **Codex edits arrive as patches.** Codex's file edits are a single `apply_patch` call carrying a patch, not a file path. gitvow parses the patch for the files it touches and the lines it adds, so path rules, route questions, attribution and snapshots work, but a rule that depends on the exact edit text sees the patch text.
- **Codex wraps its tool calls in JavaScript.** In the session file, Codex 0.15 records one `exec` tool whose input is a snippet such as `await tools.exec_command({"cmd": ...})` or `await tools.apply_patch("...")`. gitvow unwraps it, so notes and reports name `Bash` and `Edit` rather than `exec`. The hook payload itself is unaffected; this only concerns what the transcript reader can see.
- **Cursor's transcript is not read.** The stated plan and tool counts come from the transcript. gitvow reads Claude Code's format, Codex's session files (`~/.codex/sessions/.../rollout-*.jsonl`) and Gemini CLI's chat recordings (`~/.gemini/tmp/<project>/chats/session-*.jsonl`), each detected from the file itself. Cursor does not document the file behind `transcript_path`, so for Cursor the note records the commit, trailers, snapshot and attribution and leaves the plan empty. The Codex and Gemini readers follow the formats as published in each project's source and community write-ups; they are marked *unverified against a live file* until a user confirms.
- **Cursor has no hook before a file edit** other than the generic `preToolUse`; gitvow uses that for gating and `afterFileEdit` for snapshots. If Cursor's built-in edit tool bypasses `preToolUse` in a future version, the gate still sees shell commands and MCP calls through `beforeShellExecution` and `beforeMCPExecution`.
- **Copilot CLI passes no transcript path**, so its notes never carry a plan; everything else works. Factory passes one, but its format is not documented, so the same applies.
- **Cursor writes no transcript, and its hooks are fail-closed.** The note for a Cursor commit carries the trailers, decisions, attribution and snapshot, and leaves the plan, tool counts and cost empty, because those come from a transcript Cursor does not expose. gitvow installs its Cursor hooks with `failClosed: true`, so every hook answers with a JSON permission object even for events it does not use; a silent hook would block the tool.
- **Verification status.** Gemini CLI (0.61, 2026-09-29: force push refused through BeforeTool, the card shown at the commit, the commit repaired with its trailers and note because Gemini disables git hooks, see above), Claude Code, Codex CLI, Cursor and OpenCode (1.18, 2026-09-29: force push refused through the plugin, the card shown on the commit, the retry committed with session, intent and open-finding trailers and a note, the first message captured as the intent, the snapshot and the ledger written) have been exercised in real sessions end to end: the gate collects a finding, the card stops the commit, a person's answer becomes trailers, and the note carries the decision and attribution. The Copilot CLI and Factory adapters are built and tested against the payload shapes in each vendor's documentation, and marked *unverified in a real session* until someone with that agent installed runs `gitvow selftest --agent <name>` and a real session against a scratch repository. Please report what you see.

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
| OpenCode `bash` | `Bash` with `command` |
| OpenCode `edit`, `write` | `Edit` / `Write` with `file_path` (from `filePath`), `old_string`, `new_string`, `content`; `patch` passes through under its own name |
| any `mcp__server__tool` | unchanged |
