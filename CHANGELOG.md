# Changelog

## Unreleased

## 0.13.2 — 2026-09-10
- Documentation caught up with the last four releases. The quick start was still Claude-Code-only, told you to run an install that no longer behaves that way, and printed a self-check from before decisions existed. It now covers agent detection, `--check`, `gitvow status`, and what a decision card actually looks like at commit time, with the current eighteen checks.
- The agents guide names which adapters are verified in a real session and which are built from vendor documentation alone, per agent, in the table rather than in a paragraph at the end.
- Added the trap desktop agents fall into: Cursor does not inherit your terminal's PATH, so a per-repository install's bare command often cannot be found, and because its hooks are fail-closed Cursor blocks the tool instead of running ungated.
- The install guide now documents four git hooks rather than one, agent detection, staleness after an upgrade, and points at `gitvow status` instead of a grep for diagnosing silent hooks.

## 0.13.1 — 2026-09-10
- `gitvow install` with no `--agent` now finds every agent on the machine (by configuration directory, application path or command) and configures all of them, printing what it found. Forgetting a flag no longer leaves an agent silently ungated. `--check` runs the self-check straight afterwards, so the first thing a new user sees is the gate working in their own terminal.
- `gitvow uninstall` with no `--agent` removes gitvow from every agent configured in that scope, leaving any hooks of your own in place. Previously both commands silently assumed Claude Code.
- `gitvow status` detects an install left behind by an older gitvow: git hook scripts whose contents differ from what this version writes, and agent settings missing events this version installs. Either means behaviour you have upgraded to is not actually running.
- Both commands end with the next thing to do: install points at `gitvow status`, and status says plainly whether the record is live here, is ready and waiting for a first session, or is not being written at all.

## 0.13.0 — 2026-09-10
- `gitvow status`: proves the install is live rather than merely present. It checks that the hook command each agent will run actually resolves (an absolute path, or a warning that a desktop agent may not share your shell's PATH), that the policy and redaction rules load, that all four git hooks are present and executable, that the notes refs are configured, and whether any session has been recorded in this repository yet. Every failure prints the line that fixes it; exit 1 when anything fails. `--json` for scripts.
- `gitvow install` now ends with the steps it cannot do for you. For Codex that is trusting the hooks once through `/hooks` and adding the repository's `.git` to `sandbox_workspace_write.writable_roots`, either of which otherwise leaves the gate installed and inert.
- Cost is no longer presented as fact when it was guessed. A model the pricing table does not name is priced from the nearest same-family entry and now reported as `inferred_pricing` in the note and marked in the pull request report, e.g. "gpt-5.6-terra priced as gpt-5". A new generation has cost twice its predecessor before, so a silent prefix match was a figure nobody could trust.
- Removed the Codex "enable hooks" hint: hooks are on by default, and the hint told users to add a line they do not need.

## 0.12.4 — 2026-09-10
- Cursor verified in a real session, end to end: the gate collected an at-commit finding from a file edit, refused the commit with the card, and after `gitvow decide` the commit carried the trailer and a note with the decision, scope, reason and attribution. Four defects that session exposed are fixed below; none of them were visible from the vendor documentation alone.
- **The decision card is refused, not asked.** On agents with their own permission prompt the card came back as `ask`, so a click on Cursor's approve button ran the commit with the findings still open and nothing recorded but a `Gitvow-Open` trailer. On Cursor and Copilot CLI a denial and a card are now both `deny`; an immediate confirm rule such as pushing to a remote is still delivered as the person's own prompt, which is what it is for.
- **A hook with nothing to say now answers explicitly.** gitvow installs Cursor's hooks fail-closed, and Cursor treats a hook that prints nothing as a failed hook and blocks the tool. Events an adapter does not use now print `{"permission": "allow"}` rather than exiting silently.
- **Shell commands were gated twice.** Cursor fires `preToolUse` alongside `beforeShellExecution` for the same command, so every command was evaluated and logged twice and step numbers advanced twice. The specific hook wins.
- Cursor's payload contract: Cursor names every agent file modification `Write` and carries the new text as `new_content`, which the gate and providers never read, so route questions were silently skipped on Cursor; `new_content` is now exposed as `content`. Its `MCP:<tool>` naming in `preToolUse` is mapped to `mcp__<server>__<tool>`, so MCP allow and deny lists apply. `Delete` is treated as an edit, so removing a gate-bearing file is gated like changing one.
- The card no longer records a turn count when the agent passes no transcript, so a decision on Cursor is not reported as answered with nobody speaking.

## 0.12.3 — 2026-09-10
- Codex CLI verified in a real session, end to end: the gate collects an at-commit finding from an `apply_patch` edit, the card refuses the commit, `gitvow decide` records the answer, and the commit carries the trailers with a note holding the decision, attribution and cost.
- Codex transcript reader: unwraps the JavaScript snippet Codex 0.15 records for every tool call (`await tools.exec_command({...})`, `await tools.apply_patch("...")`), so notes and reports name `Bash` and `Edit` instead of a single `exec`, and patched files reach `files_written`.
- User turns are counted from `input_text` parts as well as `text`, and blocks the harness injects (`<recommended_plugins>` and the like) no longer count as a person speaking, so `human_turns_after_card` is accurate on Codex.
- Documented two Codex requirements found in that session, each of which otherwise leaves the gate installed but inert: hooks must be trusted once via `/hooks`, and the repository's `.git` must be in `sandbox_workspace_write.writable_roots` or the agent cannot commit at all.

## 0.12.2 — 2026-09-09
- `gitvow revisit <commit> [accept|decline] [--finding N]`: answer a decision already on the branch again, including a `Gitvow-Open` finding from a person's commit. Writes an empty commit with the new answer and `Gitvow-Revisits: <commit>`; the earlier trailer stays. Open findings a revisit has answered stop counting as debt.
- Digest: decisions accepted, declined, open and revisited; decision debt with the command to close each item; earned rules in force; questions per session against the previous period; findings collected and immediate confirmations; payback (snapshots restored, questions pre-answered, answers that matched the proposal). Per repository only.
- The card stores what the record proposed on each finding; the note carries it as `proposed`, and the report marks answers that matched the proposal. `gitvow restore` is logged so the digest can count it.
- Earned rules: first and last dates are by date, not history position.

## 0.12.1 — 2026-09-09
- Earned rules: `gitvow rules` derives, per repository, findings answered the same way by authorities at least `decisions.rule_threshold` times (default 3), each with count, dates, people, scopes, commits and an expiry after `decisions.rule_decay_days` (default 90) without a new confirmation. A contradicting answer resets the run; answers by people outside `decisions.authorities` do not count.
- Rules reach the agent as context: the SessionStart hook prints them to stdout (Claude Code, Codex, Gemini, Factory add it to the conversation), the card shows the rule next to the proposal, and `gitvow rules --write [--agent NAME | --file PATH]` maintains a managed section in the instruction file. Nothing is accepted on a rule's strength.
- Authorities given as emails also match trailer identities by local part.

## 0.12.0 — 2026-09-09
- Decisions. Confirm rules carry `when`: `immediate` (default for Bash rules) or `commit` (default for path and provider rules). At-commit findings accumulate in the session state while the agent works; `git commit` is refused once with a card naming each finding, its evidence and what the record proposes from earlier decisions on the same finding in the repository.
- `gitvow decide <n|all> accept|decline [--scope] [--reason] [--by]` records a person's answer; `gitvow decisions` prints the card. Answers become `Gitvow-Accepted` / `Gitvow-Declined` trailers and a `decisions` array in the session note (schema 5) with authority, scope, reason and the number of user turns between the card and the answer.
- Human commits while findings are open: `Gitvow-Open` trailers by default; `decisions.mode: strict` makes the pre-commit hook refuse until answers are recorded. New `pre-commit` and `post-commit` git hooks; re-run `gitvow install` to get them.
- `gitvow report` lists decisions per commit, reopens scoped decisions when `--target` is a production branch, and prints a `--decisions-summary` block; the action's `pr-description` input (default `always`) writes that block into the pull request description so a squash commit inherits it.
- `gitvow check` reports `CONFIRM AT COMMIT` for at-commit rules and exits 0.

## 0.11.1 — 2026-09-09
- Pricing: explicit entry for the current Opus generation after a real-session calibration showed the older-generation prefix overstated cost about two times. Calibration method documented.

## 0.11.0 — 2026-09-09
- Usage per session: input, output, cache and reasoning tokens, models seen and an estimated cost in USD, from Claude Code, Codex and Gemini transcripts. Default pricing table dated 2026-09, overridable under `pricing` in the policy. Shown in the session note (schema 4), ledger, `report`, `digest` and `handoff`.
- Subagent side conversations counted in the note; Cursor subagent events recorded in the hook log.

## 0.10.0 — 2026-09-09
- External adapter protocol: `gitvow-agent-<name>` executables on the PATH (or in `~/.gitvow/agents/`) with `info`, `normalize` and `respond` subcommands are discovered by `hook`, `install`, `uninstall` and `selftest --agent`. Missing or broken adapters block during PreToolUse and log otherwise. Example adapter under `examples/agents/`.

## 0.9.0 — 2026-09-09
- `gitvow digest [--since 7d|date] [--json]`: a period summary of the current branch from trailers, notes and the hook log: agent versus human commits, sessions with plans and attribution, human edits after agents, gate confirmations and denials by reason, files most changed by agents.

## 0.8.0 — 2026-09-09
- Adapters for GitHub Copilot CLI (camelCase payloads, `permissionDecision` JSON with `ask` for confirmations, hooks under `~/.copilot/hooks/`) and Factory Droid (Claude-compatible payloads, `Execute`/`Edit`/`Create`/`ApplyPatch` tools, `~/.factory/hooks.json`). Built against vendor documentation; real-session verification pending.

## 0.7.1 — 2026-09-09
- Transcript readers for Codex session files and Gemini CLI chat recordings, detected from the file: session notes and ledgers for those agents now carry the stated plan, tool counts and files written. Tool output is never read in any format. Cursor's transcript remains undocumented and unread.

## 0.7.0 — 2026-09-09
- `gitvow why <path>`, `gitvow trace <path>[:a-b]`, `gitvow recall <words>`, `gitvow handoff [--session]`: the record answers the next session's questions from trailers, notes, snapshots and the ledger. Read-only, local.
- Agent skills published in the common Agent Skills format at wirevow/gitvow-skills.

## 0.6.0 — 2026-09-09
- Adapters for Codex CLI, Gemini CLI and Cursor: `gitvow hook --agent <name> <Event>` normalises each agent's payload and answers in its native form (exit 2 for Codex and Gemini; `permission` JSON with `ask` for confirmations in Cursor). Codex `apply_patch` edits are parsed per file for path rules, route questions, attribution and snapshots.
- `gitvow install --user --agent <name>` / `gitvow install <repo> --agent <name>` and matching uninstall write each agent's hook configuration idempotently.
- Built against vendor documentation; real-session verification pending for the three new agents.

## 0.5.0 — 2026-09-09
- Snapshots: after every agent Edit, Write, MultiEdit or NotebookEdit the working tree is written into a commit under `refs/gitvow/snapshots/<session>/<n>`, parented on HEAD, excluding ignored files and a secrets list. Local only; the pre-push hook does not push it.
- New commands `gitvow snapshots [prune]`, `gitvow diff <session> <n>`, `gitvow restore <session> <n> [--to DIR]` (detached worktree). `uninstall --purge-snapshots`.
- Session note schema 3: `snapshot` names the last snapshot before the commit.
- Policy key `snapshots` (`enabled`, `max_per_session`, `exclude`).

## 0.4.0 — 2026-09-09
- Providers: the policy's `providers` list names programs the gate asks `gate_bearing` (edited path), `route_gate` (route literal introduced by an Edit) and `route_callers` (route literal removed by an Edit). A `yes` requires confirmation with the provider's evidence shown to the agent; a failed or malformed provider yields confirm. Protocol documented; example provider `examples/providers/static_facts.py`.
- `gitvow ask <question> <subject>` to test providers.

## 0.3.0 — 2026-09-09
- `gitvow report --base <rev> [--head <rev>]`: per-commit Markdown or JSON report pairing trailers with session notes, with attribution and a "said vs did" check of changed files against the stated plan; `--require-notes` exits 1 when a trailered commit has no note.
- GitHub Action `wirevow/gitvow@v0.3`: fetches session notes, posts the report as one upserted pull request comment and the job summary, fails on missing notes.
- `pre-push` git hook installed alongside `prepare-commit-msg`: pushes `refs/notes/gitvow/*` to the remote being pushed to. `gitvow push-notes [remote]` does it by hand. **Re-run `gitvow install` after upgrading.**

## 0.2.3 — 2026-09-09
- Fix: the step counter and recorded agent blobs carried over from earlier sessions in the same repository, so a fresh session's first commit could read `Gitvow-Step: 3`. A new session id now starts from zero; a resumed session keeps its counters. Found in an end-to-end run.

## 0.2.2 — 2026-09-09
- Fix: a commit made by a person in a terminal after (or alongside) a session received the session's trailer, because the session state outlived the session. Trailers are now added only while a `pending_commit` mark set by the gate on the agent's own `git commit` is fresh, and a note is written only when HEAD carries the trailer. Found in an end-to-end run with a real Claude Code session. **The git hook changed: re-run `gitvow install` after upgrading.**

## 0.2.1 — 2026-09-09
- Per-user install records the absolute path of the `gitvow` executable in the hook command (falling back to `python -m gitvow`), so hooks run even when Claude Code's shell lacks the virtualenv or pipx PATH. Found in an end-to-end run with a real Claude Code session. Per-repo installs keep the bare name. New `python -m gitvow` entry point.

## 0.2.0 — 2026-09-09
- Session notes now live on one ref per session, `refs/notes/gitvow/<session-id>`; parallel agents never contend and pushing `refs/notes/gitvow/*` never conflicts. 0.1 notes on `refs/notes/sessions` are still read.
- Custom redaction rules from `<repo>/.gitvow/redact-rules.json` and `~/.gitvow/redact-rules.json`; both apply. An invalid rules file fails closed: nothing is written for that event.
- Line-level attribution in the note: per-file human lines changed after the agent's last write, and `agent_share` for the commit. PostToolUse now also runs on Edit, Write, MultiEdit and NotebookEdit to record the agent's written blob.
- `install` sets `notes.displayRef` and `notes.rewriteRef` to `refs/notes/gitvow/*` so `git log --show-notes` shows notes and they follow amend, rebase and squash; `uninstall` removes the keys.
- New `gitvow redact <text>` command to test rules. Note schema field `schema: 2`.

## 0.1.2 — 2026-09-08
- Fix: `gitvow --version` reported a hardcoded 0.1.0; the version now comes from package metadata, and the packaging test asserts it matches `pyproject.toml`.

## 0.1.1 — 2026-09-08
- Fix: `default_policy.json` was missing from the wheel, so a fresh install failed closed and blocked every tool call. Now shipped as package data, with a test that installs the built wheel and runs the self-check.

## 0.1.0 — 2026-09-08
- Session trailers (`Gitvow-Session`, `Gitvow-Step`) on commits made during Claude Code sessions.
- Redacted structural session notes under `refs/notes/sessions`, with per-file agent attribution.
- Tool-call gate: deny / confirm / allow on Bash commands, edited paths and MCP tool names; optional classifier; fails closed.
- Local ledger, audit log, `collect` and `summarize` for trials, `check`, `show`, `selftest`.
- Per-user and per-repo install, idempotent and fully reversible.
