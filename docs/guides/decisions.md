# Answer the card

Nothing to set up. With gitvow 0.12 installed, an agent session that touches something the policy considers consequential produces one card at commit time.

## What you see

The agent stops before committing and shows you the findings, each with its evidence and, when the repository has decided the same thing before, a proposal:

```
Before I commit, two things need your decision:

1. I edited auth/AuthorizeWhitelistedPaths.java, an authorization file.
   This repository accepted the same edit 3 times, last by nikhil on 2026-09-02 for staging.
2. I added the route /v1/orders/export in src/api/orders.py. The facts provider says it is
   not under any whitelist pattern and the service is external.

Accept or decline each?
```

Answer in the conversation: "1 accept, 2 decline, needs security review". If one of them is not your call, say that instead of guessing — "2 is security's, not mine" — and the agent records a referral. The agent records the answers and commits. The commit carries:

```
Gitvow-Accepted: edit auth/AuthorizeWhitelistedPaths.java by nikhil
Gitvow-Declined: route /v1/orders/export in src/api/orders.py by nikhil: needs security review
```

A reason is optional and one phrase is enough. A required reason produces "ok"; an optional one produces information.

## Scope

If an acceptance holds only for an environment or a branch, say so: "accept for staging". The agent records `--scope staging`, the trailer shows `scope=staging`, and a pull request that later takes this commit to a production branch reopens the question in the report.

## When it is not your call

The third answer is a referral. It says the question reached the wrong person, which is a different fact from "nobody has decided yet" and needs a different fix: the question has to reach someone else, not sit in a reminder.

```sh
gitvow decide 1 refer --to security --reason "they own this module"
gitvow decide 1 refer                              # --to is optional
```

The commit carries `Gitvow-Referred: <finding> by <you> to=security: they own this module`. The referral closes the card and the commit goes through — holding it against the wrong person produces a wrong answer, not a right one — and the record keeps saying the finding is waiting. `gitvow digest` lists it under "Referred, waiting on someone else", apart from decision debt, and the pull request report says who it needs rather than that somebody forgot. When the right person answers, `gitvow revisit <commit> accept --by <them>` records it and the referral stops waiting.

A referral never earns anything. Three referrals are three routing failures, not a pattern worth promoting.

## Declining

A declined finding is recorded and the commit still goes through. Declining means "this is not agreed"; it does not revert the edit. Fix the code, or leave the decline on the record for the reviewer to see. A declined route without a filter on a pull request is a loud line in the report.

## The commands behind the card

The agent runs these for you. They are here for when you are the one at the keyboard.

```sh
gitvow decisions                          # the card: open findings in this repository, numbered
gitvow decide 1 accept                    # record an answer for finding 1
gitvow decide 2 decline --reason "needs security review"
gitvow decide all accept --scope staging  # every open finding, one scope
gitvow decide 3 refer --to platform       # not your call; record that, do not guess
gitvow show HEAD                          # a commit's decisions with evidence, authority and reason
```

Identity comes from `git config user.email` (its local part) or `user.name`. `--by` overrides it when you record a decision someone else made in a review.

## Committing from a terminal

If you commit by hand while the agent's findings are open, the default is non-blocking: the commit carries `Gitvow-Open: <finding>` for each undecided finding, and `gitvow report` and the digest list it as decision debt until someone runs `gitvow decide`. To make the commit wait for an answer instead, set in the repository policy:

```json
"decisions": {"mode": "strict"}
```

The pre-commit hook then prints the card and refuses until every finding is decided.

## Who may decide

By default anyone with commit access. To name the people whose decisions count when rules are later derived:

```json
"decisions": {"authorities": ["nikhil", "security@example.com", "Priya Nair"]}
```

Entries match the committer's email, its local part, or name, case-insensitively. Decisions by others are recorded with `authority: none`.

## Squash merges

If your repository squash-merges, the original commits and their trailers do not reach the target branch. Two options:

- Prefer merge commits or rebase merging for repositories where the evidence should live in history.
- Keep squashing and let the [pull request check](pull-requests.md) write the decisions summary into the pull request description. GitHub uses the description as the squash commit's message by default, so the trailers arrive on the squash commit. Leave the summary block in place when you edit the description.

## Earned rules

After the same finding has been answered the same way three times by people the policy names, with no scope attached, gitvow **proposes** it as a rule. It does not create one. A scoped answer is an exception and is set aside: three accepts for staging are three exceptions, and a rule saying the unconditional thing is fine is not something anyone agreed to.

```sh
gitvow rules                 # rules in force, proposals waiting, and what is not yet either
gitvow rules --write         # managed section in CLAUDE.md (--agent codex → AGENTS.md, gemini → GEMINI.md, cursor, copilot)
gitvow rules --json
```

A proposal becomes a rule when a person named under `decisions.authorities` accepts it:

```sh
gitvow rules accept 1 --reason "this is our pattern"   # 1 is the number `gitvow rules` printed
gitvow rules accept "edit core/authz_rules.go"          # or name the finding
gitvow rules reject 1 --reason "one-off, not policy"
gitvow rules accept --all                               # every proposal standing, in one commit
```

Accepting is itself a decision, so it is recorded like one: an empty commit carrying `Gitvow-Rule-Accepted`, plus a note on `refs/notes/gitvow/rules` holding the evidence — the run, the dates, the people, the exceptions that were set aside. `gitvow show <commit>` prints it. A verdict from someone the policy does not name is refused, not recorded and discounted: their answers still count as evidence, but they cannot write the rule.

Rejecting is not permanent and is not a one-commit veto. The proposal comes back only once a full threshold of answers dated after the rejection has accumulated, so you are shown fresh evidence rather than the evidence you already refused.

Rules in force are handed to the agent at the start of every session, so writing them into the instruction file is optional; do it when you want them reviewed in a pull request alongside the policy. Proposals are handed over too, in a section that says plainly that nobody has accepted them and that they permit nothing. A rule changes what the agent proposes, not what the gate does: the card still comes, with the rule attached. Rules lapse after ninety days without a new confirmation and the run resets on a contradicting answer; both windows are in the policy under `decisions`.

### Upgrading a repository that already has earned rules

Rules earned before this release were created by the counter, not by a person, so they arrive as proposals — with their evidence intact, still handed to the agent, and labelled as proposed. Nothing is deleted and nothing goes silent. To keep them as rules, one command from an authority adopts the lot:

```sh
gitvow rules accept --all --reason "adopting the rules earned before 0.16"
```

That writes one empty commit with a trailer per rule and a note listing the evidence for each. It is a migration you can read afterwards, which is the point: the rules your repository runs on now say who accepted them.

## Changing your mind

A decision is a row, not a lock.

```sh
gitvow revisit 4f2a9c1                      # list the decisions that commit carries
gitvow revisit 4f2a9c1 decline --reason "should not have gone to staging either"
gitvow revisit 4f2a9c1 accept --finding 2   # when the commit carries several
gitvow revisit 4f2a9c1 refer --to platform  # on reflection, not ours to answer
```

Revisiting writes an empty commit with the new answer and a `Gitvow-Revisits` trailer pointing at the original. The old trailer stays on the old commit, so `git log` shows both, the new answer is what the card and the earned rules see from then on, and an open finding from a person's commit stops counting as debt. The digest lists the debt with the exact command to close each item.

## What the digest shows

`gitvow digest` gains three lines per repository:

```
Decisions: 5 accepted · 1 declined · 2 open · 1 referred · decision debt 2 · awaiting a different person 1 · 1 revisited · earned rules in force 1 · 2 proposed rules awaiting an authority
Questions: 3 cards over 4 sessions (0.75 per session, was 1.50) · 7 findings collected · 6 immediate confirmations
Payback: 2 snapshots restored · 3 questions pre-answered by the record · 4 answers matched the proposal
```

Decision debt and referrals are listed separately underneath, with the command that closes each, because one needs a decision and the other needs a different person.

Questions per session against the previous period is the autonomy meter. Nothing in it is about a person; accept rates are never broken down by who answered.
