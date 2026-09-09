# Roadmap

gitvow is deliberately small. These are the things it should grow into, in order, with the exit test for each.

## 0.2 — Trial-hardened (shipped)
- One notes ref per session, `refs/notes/gitvow/<session-id>`, so parallel agents never contend and pushes never conflict.
- Custom redaction rules from `.gitvow/redact-rules.json` in the repository and in the home directory; invalid rules fail closed.
- Line-level attribution: lines a human changed after the agent, per file, and the agent's share of the commit.
- Notes follow amend, rebase and squash through `notes.rewriteRef`.
- Exit still open: a five-person, two-week trial produces zero secrets in collected archives on manual review.

## 0.3 — Reviewer surface (shipped)
- `gitvow report` and a GitHub Action that post each pull request's session notes as one updated comment and as the job summary.
- The check fails when a commit carries a session trailer but its note was never pushed. (A commit without any trailer is, by construction, a human commit; nothing can prove otherwise, so the check reports it rather than failing.)
- "Said versus did": each changed file checked against the agent's stated plan.
- Notes pushed automatically by a `pre-push` hook; `gitvow push-notes` for by hand.
- Exit still open: two teams review with it on; time-to-first-comment measured.

## 0.4 — Policy from facts (shipped)
- Provider protocol: the gate asks external programs `gate_bearing`, `route_gate` and `route_callers`; a `yes` requires a human, with the provider's evidence in the message. Failure means confirm. gitvow defines the contract; providers live elsewhere; an example over a static facts file ships in the repository.
- Exit met: a real agent session adding an unauthorised route to a synthetic repository is stopped, and the message names the whitelist file.

## 0.5 — Live provider (shipped)
- [gitvow-provider-facts](https://wirevow.dev/gitvow-provider-facts/): a provider over a derived fact store of routes, gates and inbound calls, in its own repository. Class-level prefix resolution, whitelist-pattern classification of new routes, callers by repository and call site, store age in every answer.
- Exit met: a real agent session removing a route with recorded callers is stopped, and the message names the calling services and call sites.

## 0.5 — Snapshots (shipped)
- Working-tree snapshot after every agent edit, under `refs/gitvow/snapshots/<session>/<n>`, parented on HEAD, never pushed, secrets excluded.
- `gitvow snapshots`, `gitvow diff`, `gitvow restore` into a detached worktree, pruning by count and age, purge on uninstall. Session notes name the snapshot that preceded the commit.
- Exit met: a real agent session's first edit is restored intact after the agent edited the same file again and a person changed it further.

## 0.6 — Adapters for other agents (shipped, real-session verification pending)
- Codex CLI, Gemini CLI and Cursor adapters: payload normalisation, native responses (Cursor `ask` for confirm), patch parsing for Codex edits, per-agent install and uninstall.
- Exit: a real session on each agent stopped by the gate and producing a trailer, note and snapshot. Claude Code met; the other three await users of those agents.

## 0.7 — The record teaches the next session (in progress)
- `gitvow why`, `trace`, `recall`, `handoff` over trailers, notes, snapshots and the ledger; agent skills in the common format for Claude Code, Codex, Cursor and Gemini CLI.
- Transcript readers for Codex and Gemini so their notes carry a plan and tool counts (done, unverified against live files); Cursor's transcript is undocumented.
- Copilot CLI and Factory adapters (done, real-session verification pending).
- Exit: a real session on a repository with history answers "why does this file look like this" from the record, and a handoff produced by one agent is picked up by another.

## 0.9 — A period in one page (shipped)
- `gitvow digest --since`: agent and human commit counts, sessions with plans and attribution, lines changed by people after agents, gate activity and most-changed files for a time window, from the record alone.

## 0.10 — Any agent (shipped)
- External adapter protocol: a `gitvow-agent-<name>` executable on the PATH with `info`, `normalize` and `respond` subcommands makes a new agent work with hook, install and selftest without changing gitvow. Fails closed during PreToolUse.

## 0.11 — What each session cost (shipped)
- Tokens and estimated cost per session from the transcripts already read, with a dated default pricing table and a policy override; in the note, ledger, report, digest and handoff. Subagent side conversations counted.

## 0.12 — Decisions (shipped, real-session verification of human-commit and fork paths pending)
- Two classes of confirm rule: immediate, and at commit. At-commit findings accumulate while the agent works and are put to a person on one card when the agent commits, with evidence and the record's proposal from earlier decisions on the same finding.
- Answers written as `Gitvow-Accepted`, `Gitvow-Declined` and `Gitvow-Open` trailers and as a `decisions` array in the note (schema 5), with the person, their authority under the policy, scope and reason. `gitvow decisions`, `gitvow decide`. Human commits: open by default, strict as an option. The pull request report lists decisions, reopens scoped ones at production branches, and writes a summary into the description of squash-merging repositories.
- Earned rules (0.12.1): findings answered the same way by authorities `rule_threshold` times become rules with evidence, dates and a decay window; handed to the agent at session start and on the card as context, never as permission. `gitvow rules [--write]`.
- Autonomy meter, payback and revisit (0.12.2): the digest shows decisions, debt, questions per session against the previous period, answers that matched the proposal and snapshots restored; the report marks decisions the record pre-answered; `gitvow revisit` answers a past decision again with an empty commit and closes open debt.
- Exit: a real session accepts one finding and declines another; a person's commit records an open finding; a branch forked afterwards sees the earlier decision on its card; a scoped decision is reopened on a pull request to main.

## Later
- A configuration-driven storage tier for the ledger (restricted repository, object store).
- Real-session verification of the five built-in adapters other than Claude Code, by users of those agents.

## Not planned
Hosting, mirroring, cloud summaries of transcripts, a web UI. gitvow is the layer those things consume.
