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

Answer in the conversation: "1 accept, 2 decline, needs security review". The agent records it and commits. The commit carries:

```
Gitvow-Accepted: edit auth/AuthorizeWhitelistedPaths.java by nikhil
Gitvow-Declined: route /v1/orders/export in src/api/orders.py by nikhil: needs security review
```

A reason is optional and one phrase is enough. A required reason produces "ok"; an optional one produces information.

## Scope

If an acceptance holds only for an environment or a branch, say so: "accept for staging". The agent records `--scope staging`, the trailer shows `scope=staging`, and a pull request that later takes this commit to a production branch reopens the question in the report.

## Declining

A declined finding is recorded and the commit still goes through. Declining means "this is not agreed"; it does not revert the edit. Fix the code, or leave the decline on the record for the reviewer to see. A declined route without a filter on a pull request is a loud line in the report.

## The commands behind the card

The agent runs these for you. They are here for when you are the one at the keyboard.

```sh
gitvow decisions                          # the card: open findings in this repository, numbered
gitvow decide 1 accept                    # record an answer for finding 1
gitvow decide 2 decline --reason "needs security review"
gitvow decide all accept --scope staging  # every open finding, one scope
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

After the same finding has been answered the same way three times by people the policy names, it becomes a rule with its dates and evidence:

```sh
gitvow rules                 # rules, candidates not yet at the threshold, lapsed rules
gitvow rules --write         # managed section in CLAUDE.md (--agent codex → AGENTS.md, gemini → GEMINI.md, cursor, copilot)
gitvow rules --json
```

Rules are handed to the agent at the start of every session, so writing them into the instruction file is optional; do it when you want them reviewed in a pull request alongside the policy. A rule changes what the agent proposes, not what the gate does: the card still comes, with the rule attached. Rules lapse after ninety days without a new confirmation and reset on a contradicting answer; both windows are in the policy under `decisions`.

## Changing your mind

A decision is a row, not a lock. `gitvow decide` on the same finding again records the new answer; the trailer on the earlier commit remains, the new commit carries the new one, and `gitvow show` on each tells the story. A dedicated `gitvow revisit` for commits already on the branch follows in a later release.
