# Decisions: who agreed, recorded

The gate asks a person before an agent does something consequential. Until 0.12 the record kept only that the question was asked. The answer, the acceptance itself, is the row the whole record rests on. gitvow now writes it.

## Two moments to ask

Not every finding needs to interrupt the agent when it happens.

| Class | Examples | When the person is asked |
|---|---|---|
| **Immediate** | force push, history rewrite, `terraform apply`, `kubectl delete`, pushing to a remote, editing gitvow's own policy | at once, as before, because nothing after them can undo them |
| **At commit** | a new route without a filter, an edit to an authorization file, a change to production values or CI, a provider answering yes | when the agent runs `git commit`, all findings on one card |

An at-commit finding is not live until it is committed, so the agent keeps working and the finding accumulates silently in the session state. One question per commit replaces several per session. Rules choose their class with `"when": "immediate"` or `"when": "commit"`; Bash rules default to immediate, path and provider rules to commit. See [Policy schema](../reference/policy.md).

## The decision card

When the agent runs `git commit` with open findings, the gate refuses the commit once and hands the agent a card: each finding, why it was raised, the evidence, and what the record proposes based on earlier decisions on the same finding in this repository.

```
DECISIONS REQUIRED before this commit: 2 findings from this session.
Put this card to the user. Record each answer with
  gitvow decide <n> accept|decline [--scope <env-or-branch>] [--reason "<phrase>"]
then run the commit again.

1. edit auth/AuthorizeWhitelistedPaths.java
   why: edits an authorization or gate-bearing file
   record: accepted 3 times, last by nikhil on 2026-09-02 (scope staging). Proposed: accept.
2. route /v1/orders/export in src/api/orders.py
   why: provider facts: route_gate /v1/orders/export: not under any whitelist pattern; service is external
   record: no earlier decision.
```

The agent puts the card to the person. The person answers in the conversation, one word or one phrase each. The agent records the answers with `gitvow decide` and commits again. The card is the only surface a developer meets: no new command to learn, no rules to curate.

A proposal is context, not permission. Nothing is accepted on the record's behalf in this release.

## Decisions in git

The answers become trailers on the commit and a `decisions` array in the session note:

```
Gitvow-Accepted: edit auth/AuthorizeWhitelistedPaths.java by nikhil scope=staging: reviewed the pattern
Gitvow-Declined: route /v1/orders/export in src/api/orders.py by nikhil: needs security review
```

`git log` answers who agreed to what. `gitvow show` adds why: the evidence, the person's authority under the policy, the reason and the scope. Trailers travel with the commit through merge and rebase and are inherited by every branch forked afterwards, so the same finding on a later branch arrives on the card with its history.

### Who counts as an authority

The policy may name people or teams under `decisions.authorities`, matched against the committer's email, its local part, or name. If none are named, anyone with commit access is an authority and the note says so. A decision by someone outside the list is still recorded, marked `authority: none`; it informs, it does not earn anything.

### Scope

"Accepted, staging only" is a condition, not a blanket yes. `--scope staging` or `--scope release/2026-09` records it. The pull request report reopens a scoped decision when the pull request targets a production branch the scope does not cover: the reviewer sees "accepted for staging; this pull request targets main". A decision never widens itself.

## When a person commits from a terminal

Agent hooks do not fire; git hooks do. The agent's findings from the session are still in the repository state.

- **Default, open.** The commit goes through with a `Gitvow-Open: <finding>` trailer for each undecided finding. A person committing is not thereby deciding, and a hook that asks questions breaks IDEs and scripts. Open findings appear in `gitvow report` and the digest as decision debt, and `gitvow decide` closes them later.
- **Strict.** With `"decisions": {"mode": "strict"}` in the repository policy, the pre-commit hook prints the card and refuses the commit until `gitvow decide` has recorded an answer for every finding.

A person working without an agent triggers nothing. gitvow adds no ceremony to sessions that did not happen.

## Through the merge

| Merge style | Trailers | Session note |
|---|---|---|
| Merge commit or rebase | intact on the original commits, now on the production branch | intact; `notes.rewriteRef` follows rebases |
| Squash | in the squash commit only if its message includes the original messages, or the pull request description | lost from the new commit; the pull request comment remains the evidence |

Prefer merge commits or rebase where the evidence, not only the decision, should reach history. For repositories that squash, the [pull request check](../guides/pull-requests.md) writes a decisions summary into the pull request description so the squash commit inherits it.

## Earned rules

When authorities have answered the same finding the same way three times running (`decisions.rule_threshold`), the finding becomes an **earned rule**. A rule carries its evidence: the count, the first and last date, who decided, the scopes, the commits. It lapses when nobody has confirmed it within ninety days (`decisions.rule_decay_days`), and a contradicting answer resets the run. Three accepts by someone the policy does not name are data, not a rule.

A rule is context, not permission. It reaches the agent in two ways:

- At session start, the hook hands the rules to the agent as context: "this repository has declined every unfiltered route four times; propose the filter first". The agent can avoid raising the finding at all.
- On the card, the finding arrives with its rule attached, next to the proposal.

Nothing is accepted on the strength of a rule. The gate still asks at commit. `gitvow rules` lists rules, candidates and lapsed rules; `gitvow rules --write` puts the block into the repository's agent instruction file, in a managed section you can commit and review like the policy.

## What this is for

Every answered card is a labelled pair: a situation with evidence, and a decision with a reason, made by a named person doing their normal work. Per repository, those pairs become proposals on the next card. Later they become earned rules the agent reads as context, and a payback line in the digest. Nothing in this release suppresses a question; the record has to be honest before it is allowed to be quiet.
