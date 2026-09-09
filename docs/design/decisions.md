# Design: decisions (0.12, proposed)

*Status: agreed in discussion on 2026-09-09, not yet built. Three choices remain open at the end. Everything here is docs-first: the code will be held to this page.*

## Why

The gate stops an agent at the moment of consequence and asks a person. Today the question is asked, the person answers in the conversation, and the record keeps only that the question was asked. The answer, the acceptance itself, is the row the whole thesis rests on and the one row we do not write. Capturing it turns every confirmation into a labelled pair, situation and decision with a reason, produced by people doing their normal work. That is the raw material for memory per repository, later for an organisation, later still for a local model that predicts the organisation's own judgment and is tested against the record.

## Principles

1. **Developers' cognitive budget for this is zero.** No new command to remember, no rules to curate, no scoreboard. A tool that gives developers a second job gets uninstalled.
2. **Questions are cheap, so they need not be rare.** A question that arrives with evidence and a proposed answer costs one word. The expensive interruption is the one with no context.
3. **Decisions travel with the code.** A decision lives in the commit and in the session note, reaches the production branch with the merge, and is inherited by every branch forked afterwards.
4. **No suppression in this milestone.** The record must stay honest before it is allowed to be quiet. Auto-accepting on the strength of earned rules is a later, per-rule, platform-team switch, off by default.
5. **Per repository, never per person.** Metrics, meters and rules are about a repository's agents. Individuals are never ranked, because the instant accept rates look like performance reviews, the labels become worthless.

## The mechanism

### Two classes of gate rule
- **Immediate**: irreversible actions. Asked at once, as today, because nothing after them can undo them.
- **At-commit**: code consequences, which are not live until committed. A new route without a filter, an edit to a gate-bearing file, a provider answering yes. Accumulated silently in the session state; the agent keeps working.

### The decision card
When the agent runs `git commit`, the PreToolUse hook presents the accumulated findings as one card: each finding, its evidence, and the record's proposed answer, drawn from prior decisions on the same finding. The agent puts the card to the person. The person answers in the conversation, one word or one phrase. The agent re-runs the commit.

One question per commit instead of several per session. The card is the developer-facing surface; there is no other.

### Decisions in git
The prepare-commit-msg hook writes the answers as trailers:

```
Gitvow-Accepted: route /v1/orders/export by nikhil (staging only)
Gitvow-Declined: edit auth/AuthorizeWhitelistedPaths.java by nikhil (needs security review)
```

The session note carries the same in a `decisions` array with the finding, the evidence, the person, their authority under the policy, the reason and an optional scope. Capture of the answer comes from the transcript: the human message after the card and the agent's re-run of the commit.

### A person commits from a terminal
Agent hooks do not fire; git hooks do. The pre-commit hook prints the card in the terminal. Two modes, chosen per repository:
- **Default, non-blocking**: `Gitvow-Open: <finding>` trailers record that findings existed and nobody decided. The digest lists them as decision debt for the platform lead. `gitvow revisit` closes them later. A person committing is not thereby deciding, and interactive git hooks break IDEs and scripts.
- **Strict**: the commit is refused until `gitvow decide` has recorded an answer.

A person working without an agent triggers nothing. gitvow adds no ceremony to sessions that did not happen.

### Through the merge and beyond
| Merge style | Trailers | Session note |
|---|---|---|
| Merge commit or rebase | intact on the original commits, now on the production branch | intact; `notes.rewriteRef` follows rebases |
| Squash | carried into the squash commit's body from the commit messages | lost from the new commit; the PR comment remains the evidence |

For squash-merging repositories the PR check writes a decisions summary into the PR description so the squash commit inherits it. The docs recommend merge or rebase where the evidence, not just the decision, should reach history.

Branches forked from production inherit every decision in their history. `gitvow why` shows them; earned rules are derived from them; the same finding on a fork arrives pre-answered with the prior decision and its author.

### Scope and reopening
"Accepted, staging only" is a condition, not a blanket yes. Decisions carry an optional scope: an environment or branch. The PR check on the production branch reopens any decision whose scope is narrower than where the code is going: "accepted for staging; this PR targets production. Accept for production?" Providers reopen decisions whose underlying condition moved, for example a whitelist that changed, because they read the GitOps repository. A decision never silently upgrades itself; broader scope always needs a new answer. Decisions never cross repositories on their own.

### Earned rules
`gitvow rules` derives, per repository, findings answered the same way N times by people the policy names as authorities. Each rule carries its evidence, dates and a decay window, and is written into a managed section of the agent's instruction file as **context, not permission**: "this repository has declined every unfiltered route; propose the filter first." Rules without a recent confirmation decay back to asking. Three accepts by someone without authority are data, not a rule.

### The autonomy meter and payback
The digest and the PR comment gain: questions per session over time, decisions accepted and declined, decision debt, and a payback line: snapshots restored, questions pre-answered, work recovered. Per repository only.

### Revisit
`gitvow revisit <commit>` reopens a decision; the record keeps both. A wrong answer costs one command, which is what makes people answer freely.

## Consumption: who reads the record, when, and what changes

| Consumer | Moment | Surface | Impact measured |
|---|---|---|---|
| The agent, before acting | every tool call | earned rules in the instruction file; the pre-answered card | questions per session falling |
| The agent, before starting | first minute | `recall`, `why`, the previous handoff | fewer duplicated attempts |
| The engineer | when something looks off or is lost | `why`, `trace`, snapshots, the card | time to recover; whether they keep it installed |
| The reviewer | reading a PR | the PR comment: plan, attribution, decisions, cost | time to first comment |
| The platform lead | Monday | the digest | which rules to relax or tighten |
| The VP | quarterly, or an incident | the decision history; the autonomy curve | "who agreed" answered in minutes |
| Security and audit | review, incident | the record as evidence | preparation from weeks to hours |
| A new engineer or agent | first week | earned rules, recall | onboarding without tribal knowledge |
| The future local model | training | (situation, decision, reason) triples | predicting the organisation's own judgment |

Push beats pull: the digest arrives where the platform lead already reads, four lines, weekly. The PR comment already does this for reviewers. Dashboards are not planned.

## Engagement without gamification

Kept: one question per commit; proposed answers with evidence; the per-repository autonomy meter; the payback line; a PR badge as social proof ("recorded · 2 decisions accepted by a named engineer · 0 open"); decision debt shown once, never nagged; cheap undo; team modes chosen once by the platform team; milestones that mark trust ("100 decisions recorded; agents may now auto-accept staging helm upgrades"), announced in the digest.

Refused: leaderboards, per-person accept rates in reports, points, streaks, anything that makes answering a question feel like scoring.

## Failure modes and their answers

- **Rules rot**: every rule carries evidence and dates, decays without recent confirmation, and reopens when the condition behind it moves.
- **Learning the wrong lesson**: authority is required to earn a rule.
- **Over-suppression**: no suppression in this milestone; later, per rule, off by default, reported as loudly as denials.
- **Gaming**: rules bind on provider facts, not on the agent's stated plan; the said-versus-did check exists for this.
- **Privacy**: per-user rules are visible to that user and used by their own sessions; beyond them, only anonymised counts.

## Out of scope
Suppression, per-person metrics, rankings, per-user memory beyond the user, cross-repository rule sharing (the storage-tier milestone), the local model.

## Steps and exit tests

1. Rule classes, immediate and at-commit; accumulation in session state. *Exit: an agent adds an unauthorised route and keeps working; nothing blocks until commit.*
2. The decision card at commit with proposed answers. *Exit: one card per commit naming each finding, its evidence and a proposal.*
3. Trailers and note fields for decisions, with scope; capture from the transcript; `Gitvow-Open` for human commits; strict mode; PR description summary for squash repositories. *Exit: `git log` shows who accepted what; `gitvow show` shows why.*
4. Earned rules with evidence, dates and decay, written as context. *Exit: after five identical accepts, the next card arrives pre-answered.*
5. Autonomy meter and payback in digest and PR comment. *Exit: the trial repository's digest shows the curve.*
6. `gitvow revisit`. *Exit: a wrong answer costs one command.*
7. Real-session verification: accept, decline, a human commit, a fork inheriting a decision, the production-boundary reopen.

## Open choices

1. **Immediate list.** Proposed: force push, history rewrite, infrastructure destroy or apply, cluster mutation, deletes, pushing to a remote. Everything else at-commit.
2. **Authority.** Proposed: people or teams named in the repository policy; if none are named, anyone with commit access, and the digest says so.
3. **Reasons.** Proposed: optional, one phrase, because required reasons produce "ok".
