# Session notes on pull requests

GitHub does not display git notes. gitvow puts them where reviewers already look: a comment on the pull request and the check's job summary.

## What the reviewer sees

```
## gitvow: 2 agent commits, 1 human commit

### 1637b25 Fix add  — session bd7c6965, step 1
**Plan:** Change the cache key to include week start so per-org week settings do not collide.
**Tools:** Bash, Edit, Read · 3 tool calls · 4 turns
**Attribution:** 1 file, agent share 1.00, 0 lines changed by a human after the agent
**Said vs did:** calc.py mentioned in plan ✓

### 0977d2f Add sub  — session a28bd848, step 1
**Plan:** (none stated before committing)
**Attribution:** 1 file, agent share 0.50, 4 lines changed by a human after the agent
**Said vs did:** calc.py not mentioned in plan · no plan to compare

### b356229 human tweak — no session trailer (made by a person)
```

Read it top down. A commit with a plan that names the files it changed and a high agent share is what it claims to be. A commit whose plan is empty, or whose changed files the plan never mentioned, or whose agent share is low, deserves the diff read with more care. A commit without a trailer was made by a person and is reviewed the ordinary way.

## Setting it up

Add one workflow to the repository:

```yaml
# .github/workflows/gitvow.yml
name: gitvow
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: wirevow/gitvow@v0.3
```

The action fetches `refs/notes/gitvow/*`, runs `gitvow report` over the pull request's commits, posts the report as a single comment that it updates on every push, writes the same report to the job summary, and **fails the check when a commit carries a session trailer but its note is missing**, which means the author's notes were never pushed.

Inputs, all optional:

| Input | Default | Meaning |
|---|---|---|
| `require-notes` | `true` | fail when a trailered commit has no note |
| `comment` | `true` | post or update the pull request comment |
| `version` | the action's own tag | gitvow version to install |
| `fail-under` | none | fail when `gitvow coverage` is below this percentage: agent-signed commits in the range that carry no session |
| `pr-description` | `always` | write the decisions trailer block into the pull request description so a squash commit inherits it; `never` to skip; `auto` writes it only when the workflow token can read the merge settings and the repository is squash-only, which default tokens cannot |

## Decisions on the pull request

Each agent commit's report lists the decisions it carries: what was accepted or declined, by whom, with what scope and reason, any `Gitvow-Open` finding nobody decided, and any `Gitvow-Referred` finding that reached the wrong person. Referrals are counted and worded separately from open findings, because a reviewer who reads "3 open" chases the team and a reviewer who reads "3 waiting on security" chases security. When a decision's scope does not cover the branch the pull request targets and that branch is in `decisions.production_branches`, the report reopens it: "accepted for staging; this pull request targets main". The check does not fail on it; the reviewer decides.

### Squash merges

A squash merge writes one new commit whose message GitHub takes from the pull request title and description. The original commits' trailers do not reach the target branch unless they are in that message. The action therefore writes a block into the pull request description on every push:

```
<!-- gitvow-decisions -->
Gitvow-Accepted: edit auth/AuthorizeWhitelistedPaths.java by nikhil scope=staging
Gitvow-Declined: route /v1/orders/export in src/api/orders.py by nikhil: needs security review
Gitvow-Referred: edit infra/iam/roles.tf by nikhil to=platform: they own this module
<!-- /gitvow-decisions -->
```

The block is updated on every push and can be edited around. A squash commit inherits the trailers; the session notes do not follow, so the comment stays as the evidence. Where the repository merges or rebases, the block is redundant and harmless, and `pr-description: never` turns it off. Where evidence should reach history, prefer merge commits or rebase merging. To produce the block by hand: `gitvow report --base origin/main --decisions-summary`.

## Getting notes to the remote

Notes live on `refs/notes/gitvow/<session-id>` and git does not push them with a branch. `gitvow install` adds a `pre-push` hook that pushes those refs to the same remote every time you push, so nothing changes in your workflow. To push them by hand:

```sh
gitvow push-notes            # current remote (origin)
git push origin 'refs/notes/gitvow/*'
```

## Running the report locally

```sh
gitvow report --base origin/main --head HEAD          # markdown to stdout
gitvow report --base origin/main --head HEAD --json   # machine-readable
gitvow report --base origin/main --require-notes      # exit 1 when notes are missing
```

## Said versus did

The comparison is deliberately simple and transparent: it checks whether each changed file's name appears in the agent's last stated plan before the commit. It cannot judge whether the change is correct; it tells the reviewer where the agent's stated intent and its actual edits diverge, and where no intent was stated at all. Anything smarter belongs in a separate reviewer, fed by this report.
