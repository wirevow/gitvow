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
If the person says this is not their call, record that instead of guessing:
  gitvow decide <n> refer [--to <person-or-team>] [--reason "<phrase>"]
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

### What makes two findings the same finding

The record groups by the finding's text, so what that text names decides what can ever accumulate. It names the thing being decided about, never the particular occasion: a path for an edit, a route for a route, and — since 0.16 — the program and the rule that stopped it for a command, so `run kubectl (cluster mutation)` covers `kubectl apply -f a.yaml` and `kubectl apply -f b.yaml` alike. The command line itself is evidence on the card, which is where the per-invocation detail belongs.

Before 0.16 a command finding was the whole command line. That made every invocation a fresh question with no record behind it, and no command could ever reach `rule_threshold`, because the arguments are exactly the part that changes each time. Command decisions recorded by 0.15 and earlier still read and still parse; they simply stay in their own per-command-line groups, which is where they always were.

## Four answers, because there are four situations

| Answer | What it says | What it costs later |
|---|---|---|
| **accept** | agreed, unconditionally | can become precedent |
| **decline** | not agreed | can become precedent |
| **refer** | *I am not the person to decide this* | nothing; the question has to reach someone else |
| *(open)* | nobody answered at all | decision debt until someone does |

The last two used to be one. Both arrived on a commit as `Gitvow-Open`, which meant a queue of questions
aimed at a person who could never answer them looked exactly like a team that was behind on its answers, and
the two need opposite remedies: debt needs a decision, a referral needs a different person. A referral is now
its own trailer and its own state, counted and listed separately in the digest and the pull request report,
and it never counts towards a proposed rule — not because it is unhelpful, but because "ask someone else" is
not an answer about the change.

A referral closes the card and lets the commit through. Holding a commit against the wrong person produces a
wrong answer, not a right one, so the pressure is put where it belongs: on the record, which says out loud
that this finding is still waiting on `security`. `--to` is optional; a referral with nobody named is still
better than an accept nobody believed.

## Decisions in git

The answers become trailers on the commit and a `decisions` array in the session note:

```
Gitvow-Accepted: edit auth/AuthorizeWhitelistedPaths.java by nikhil scope=staging: reviewed the pattern
Gitvow-Declined: route /v1/orders/export in src/api/orders.py by nikhil: needs security review
Gitvow-Referred: edit infra/iam/roles.tf by nikhil to=platform: they own this module
```

`git log` answers who agreed to what. `gitvow show` adds why: the evidence, the person's authority under the policy, the reason and the scope. Trailers travel with the commit through merge and rebase and are inherited by every branch forked afterwards, so the same finding on a later branch arrives on the card with its history.

### Who counts as an authority

The policy may name people or teams under `decisions.authorities`, matched against the committer's email, its local part, or name. If none are named, anyone with commit access is an authority and the note says so. A decision by someone outside the list is still recorded, marked `authority: none`; it informs, it does not earn anything.

### Scope

"Accepted, staging only" is a condition, not a blanket yes. `--scope staging` or `--scope release/2026-09` records it. The pull request report reopens a scoped decision when the pull request targets a production branch the scope does not cover: the reviewer sees "accepted for staging; this pull request targets main". A decision never widens itself.

A scoped answer is therefore an exception, and an exception is never a precedent: it is set aside before any rule is counted. Three accepts for staging are three exceptions, not a rule that the unconditional thing is fine — nobody ever said that. A finding's exceptions are counted and shown next to it, because a finding collecting exceptions and never a plain answer is worth a person's attention, but they cannot add up to a rule in either direction and they do not break a run of plain answers either.

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

## Earned rules, and who is allowed to write one

When authorities have answered the same finding the same way three times running, with no scope attached (`decisions.rule_threshold`), gitvow **proposes** the finding as a rule. It does not create one.

That distinction is the whole of it. Automatic promotion at a threshold conflates four different kinds of decision:

| Kind | Example | Where it belongs |
|---|---|---|
| Universal best practice | do not log secrets | the model and the linter already know; earning it adds nothing and dilutes the set |
| Organisation-wide standard | every service authenticates through the gateway | the organisation, pushed down into repositories — not inferred upwards from one of them |
| Repository or path specific | this module's routes always go through the whitelist | **here**; this is what a rule is for |
| Situational one-off | this exception, staging only | nowhere. Three accepts of an exception is not a rule, it is three exceptions |

And there is a constitutional problem with promotion by counting. This project's claim is that authority is human and the machine never accepts its own consequence. A rule created because a counter reached three is binding policy that nobody accepted — the machine authoring policy. Counting is gitvow's job. Deciding that a count means something is not.

So the threshold produces a proposal, and a person named under `decisions.authorities` accepts or rejects it:

```sh
gitvow rules                       # rules in force, proposals waiting, and what is not yet either
gitvow rules accept 1 --reason "this is our pattern"
gitvow rules reject 1 --reason "one-off, not policy"
gitvow rules accept --all          # every proposal standing, in one commit
```

Because creating precedent is itself a decision, the verdict is recorded the way decisions are: a `Gitvow-Rule-Accepted` or `Gitvow-Rule-Rejected` trailer on an empty commit, and a note on `refs/notes/gitvow/rules` holding the evidence the verdict was made against — the run, its dates, the people, the exceptions set aside. Someone reading the repository in a year can see not only that a rule was accepted but what was in front of the person who accepted it. Unlike a decision, a verdict from someone the policy does not name is refused rather than recorded and discounted: an answer by the wrong person is still a fact about what happened, but a rule by the wrong person is exactly what this mechanism exists to prevent.

A rejection is neither a veto for one commit nor permanent. It sets a floor: the proposal returns only once a full threshold of answers dated **after** the rejection has accumulated. Fresh evidence, rather than a re-count of the evidence already refused.

A rule still lapses after ninety days without a new confirmation (`decisions.rule_decay_days`), and revives when it is confirmed again; the acceptance stands. A contradicting answer resets the run, and the new run is a new proposal, because accepting "declined three times" was never an acceptance of "accepted three times".

### How this reaches the agent

- At session start, the hook hands the agent the rules in force: "this repository has declined every unfiltered route four times; propose the filter first."
- Proposals are handed over too, in their own section, labelled: nobody has accepted this, it grants nothing, it is not the repository's position, and it changes nothing about what must be put to a person. They are there so that nothing which used to be visible goes quiet, not as permission in waiting.
- On the card, the finding arrives with its rule attached. If a proposal is standing instead, the card says so and names the command an authority would use, so the governance step surfaces at the moment someone is already looking.

Nothing is accepted on the strength of a rule or a proposal. The gate still asks at commit. `gitvow rules --write` puts the block into the repository's agent instruction file, in a managed section you can commit and review like the policy.

**The danger was never too few rules. It is too many bad ones.** A set padded with platitudes, one-off exceptions and calls made by people who were not the right ones to make them produces a brief the agent should ignore, or worse, obeys. That is the disease that kills a hand-maintained wiki, arriving by another road.

## The autonomy meter and payback

The digest and the pull request report show, per repository and never per person: decisions accepted, declined, referred and open; decision debt, the open findings nobody has answered; referrals, the findings waiting on a different person; proposed rules awaiting an authority; questions per session against the previous period; how many answers matched what the record proposed; snapshots restored. A repository whose questions per session fall while its accepted decisions rise is one whose agents are learning what it wants, and the numbers say so without anyone being ranked.

## Revisit

`gitvow revisit <commit> accept|decline` answers a decision already on the branch again, including an open one from a person's commit. It writes an empty commit carrying the new answer and `Gitvow-Revisits: <commit>`; the earlier trailer stays where it was, so the record keeps both and the debt is closed. A wrong answer costs one command, which is what makes people answer freely.

## What this is for

Every answered card is a labelled pair: a situation with evidence, and a decision with a reason, made by a named person doing their normal work. Per repository, those pairs become proposals on the next card. Where the pattern is strong enough, gitvow proposes it as a rule and a person accepts it, and only then does the agent read it as the repository's position. Nothing in this release suppresses a question; the record has to be honest before it is allowed to be quiet, and it never writes its own policy.
