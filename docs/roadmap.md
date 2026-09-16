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
- Exit: a real session on each agent stopped by the gate and producing a trailer, note and snapshot. Claude Code, Codex CLI and Cursor met (Codex and Cursor verified 2026-09-10; each real session exposed defects the vendor documentation could not, from Codex's hook trust and `.git` sandbox to Cursor's clickable-past card and silent-hook block); Gemini CLI awaits a user of that agent.

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
- Earned rules (0.12.1): findings answered the same way by authorities `rule_threshold` times, each with evidence, dates and a decay window; handed to the agent at session start and on the card as context, never as permission. `gitvow rules [--write]`. Since 0.16 the threshold *proposes* a rule rather than creating one (below).
- Autonomy meter, payback and revisit (0.12.2): the digest shows decisions, debt, questions per session against the previous period, answers that matched the proposal and snapshots restored; the report marks decisions the record pre-answered; `gitvow revisit` answers a past decision again with an empty commit and closes open debt.
- Exit: a real session accepts one finding and declines another; a person's commit records an open finding; a branch forked afterwards sees the earlier decision on its card; a scoped decision is reopened on a pull request to main.

## 0.16 — Nobody's policy but a person's (shipped)

Promotion at a threshold made gitvow the author of binding policy, in a project whose claim is that authority
is human and the machine never accepts its own consequence. It also could not tell a repository-specific call
from a platitude, an organisation-wide standard or a one-off exception, and a rules set padded with all three
is a brief the agent should ignore or, worse, obeys.

- The threshold proposes; an authority accepts or rejects with `gitvow rules accept|reject`, recorded as a
  trailer plus a note carrying the evidence, because creating precedent is a decision. A rejection waits for
  a full threshold of fresh evidence before the proposal returns.
- A scoped answer is an exception and never counts towards a rule in either direction.
- `gitvow decide <n> refer [--to who]`: "you asked the wrong person" separated from "nobody has decided",
  with its own trailer and its own line in the digest and the pull request report.
- Still missing, and deliberately: organisation-wide rules pushed *down* into repositories. That is where
  the second kind of decision belongs, and there is no organisation store yet.
- Exit: a repository upgrading from 0.15 keeps every rule it was using, as a proposal its authority adopts in
  one command, and the rules it runs on afterwards each name the person who accepted them.

## 0.19 — Claims, what the owners said (shipped)

- A claim is a statement a person made about their system, in their own words, with a source, bound to paths.
  Candidates arrive from a batch job outside this package (sessions, documents) as a JSONL file; `gitvow claims
  import` queues the repo-reach ones locally and keeps person-reach preferences in the speaker's own
  `~/.gitvow/claims/`, never in git. `gitvow claims` is the queue, most confident first. `confirm` and `reject`
  are empty commits carrying two new trailer names, `Gitvow-Claim-Confirmed` and `Gitvow-Claim-Rejected`, with a
  note on `refs/notes/gitvow/claims` holding the verbatim text, the edit if any beside the original, the source
  pointer and who confirmed with what standing. An older gitvow matches neither trailer, which is the safe
  direction.
- Confirmed claims are context, never permission: rendered into the agent's instruction files under their own
  managed section, attributed and dated, and handed over at session start with the person's own preferences.
  The gate still asks.
- Why: the departure pass. A departing engineer's knowledge of their systems leaves with them unless it is said,
  extracted, put back in front of them in their own words, and confirmed. The extraction runs outside this
  package; this release is where a confirmation lands, and it is the same primitive a rule verdict uses.
- Exit: a confirmed claim reads with `git log` and `gitvow show` alone; a later rejection withdraws it from every
  rendered surface; nothing person-reach ever appears in the repository or a bundle.

## 0.18 — The price of a question (shipped)

- Replaying three engineers' real sessions (15,549 tool calls) through the default policy priced the gate at
  19 questions per engineer per week, two thirds of them `git push`, asked on every push and able to earn
  nothing because an immediate confirm recorded no finding. That number is an uninstall, and an uninstalled
  gate records nothing. Three changes, all inside one repository and all free.
- **`when: observe`**, a third tier. The finding is recorded on the commit as `Gitvow-Observed`, counted by the
  digest and the report, and never put to anyone. Zero interruptions; the record grows; a team moves a pattern
  to `commit` once the record shows it recurs and matters. Never precedent.
- **An immediate confirm is answered once per session.** It is recorded as a numbered finding; `gitvow decide
  <n> accept --scope session` (or the agent's own approve button, which PostToolUse now records) holds for the
  session and rides on the next commit as a scoped decision, which is an exception and never a rule. A decline
  blocks for the session. A confirm nobody answered leaves nothing on the commit, because nothing ran.
  `decisions.session_scope: false` asks every time, as before.
- **Edits outside the repository are counted and named.** 28% of edits in the replay went to a different
  checkout than the session's own. They are now counted per target repository in the session state, named on
  the card, and written into the note as `edits_outside_repository`.
- Note schema 7, additive. The `prepare-commit-msg` and `pre-commit` hook bodies changed: **re-run `gitvow
  install` in every repository**; `gitvow status` names the ones still on the old body.
- Exit: the same replay against the 0.18 default asks once per session for a push instead of once per push,
  and the observe tier lets the loop propose rules that cost nothing to adopt.

## 0.17 — Two modes for the agent's commit (shipped)

- `decisions.mode` had governed only a person's commit: `open` let it through with `Gitvow-Open` trailers, `strict`
  refused it. The agent's commit was refused by the card in both. So the tool was advisory for people and
  blocking for agents, and nobody had chosen that. The same setting now governs both. In `open`, the default,
  the agent's commit is stopped once with the card, and a second attempt with nothing new pending goes through
  carrying every unanswered finding as `Gitvow-Open`. In `strict` it is refused until every finding is answered.
- On agents with a native approve button the open card is delivered as a question, so approving it is the
  answer "let it through"; the strict card stays a refusal there, as before.
- Why the default is open: a gate that blocks by default gets uninstalled, and an uninstalled gate records
  nothing. "Nobody decided" is more useful as a count in the digest than as a wall. Strict is one line in the
  policy, and a repository that wants the wait can have it.
- Exit: the same session, the same finding, the same commit command, run under each mode, produces `Gitvow-Open`
  on the commit in one and no commit at all in the other; and the open card is `ask` where the strict card is
  `deny` on every adapter with a permission object.

## Signed decisions (after an organisation store exists)

The record can say who agreed. It cannot yet say that they did. A `Gitvow-Accepted` trailer is written by
the hook, but nothing stops a person writing the same line by hand with every hook live, and `gitvow report`
will then print it as fact. A hand-written `Gitvow-Session` trailer even raises `gitvow coverage`, so the one
attack a signature would prevent currently improves the number meant to detect it. This is the wrong way
round: a *deleted* record leaves a hole anyone can see, and a *forged* one does not.

- Sign the decision at the moment it is made, on the developer's machine, and verify signatures wherever the
  record is read: `gitvow decisions`, `gitvow report`, `gitvow coverage`. Keyless signing over the existing
  supply-chain rails is the intended shape, so an acceptance becomes an attestation whose subject is the
  commit and whose predicate is the decision — no new infrastructure to trust, and admission control gets it
  for free.
- Sequenced behind the organisation store rather than ahead of it: a signature is verified against something,
  and until a store holds keys and identities the only verifier is the same machine that wrote the trailer.
  Coverage's corroboration column is the interim.
- Coverage grew a corroboration column first (0.16), which is the cheap half of this and needs no keys: a
  session trailer is compared against the note the hook would have written, and claims with nothing behind
  them are counted and listed separately. It raises the floor from "type one line" to "understand the note
  refs". It is not proof — a forger can write a note too — and the numbers say so.
- Exit: a decision trailer altered or added by hand is reported as unverifiable by every command that reads
  the record, on a repository where the genuine decisions still verify; and the failure mode when no keys are
  configured is a stated absence, never a silent pass.

## Later
- A configuration-driven storage tier for the ledger (restricted repository, object store).
- Real-session verification of the five built-in adapters other than Claude Code, by users of those agents.

## Not planned
Hosting, mirroring, cloud summaries of transcripts, a web UI. gitvow is the layer those things consume.
