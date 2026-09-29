# Did the decision hold?

Every decision in the record is a label with no ground truth. Someone accepted an edit to an authorization file, someone declined a route without a filter, and nothing says whether that was right. Since 0.31, `gitvow outcomes` grades each decision-bearing commit by what the world did with it afterwards, and writes the grade beside the decision.

```sh
gitvow outcomes                    # grade the last 90 days; write one note per commit
gitvow outcomes --since 6m
gitvow outcomes --dry-run          # grade and print, write nothing
gitvow outcomes --no-scm           # do not ask the forge; git alone speaks
gitvow outcomes --json
```

## What it reads

Two sources, both read-only, and every note names which one spoke.

- **The pull-request stream.** On a repository whose `origin` is on github.com, `gh api` is asked which pull request carried the commit and whether it was merged or closed unmerged. Only that endpoint, only GET. Other forges are not read in this version; the note then says `source: git`.
- **Git history.** Whether the commit is reachable from a production branch (`decisions.production_branches`), which means it landed without a pull request, and whether a later commit reverts it (`This reverts commit <sha>`), and how long after it landed.

## The grade

| answer | landed, not reverted in the window | reverted in the window, or closed unmerged | still open, or unknown |
|---|---|---|---|
| accepted | **held** | **did not hold** | pending |
| declined | **overridden** | **held** | pending |
| referred | pending | pending | pending |

Landed means merged through a pull request or reachable from a production branch directly. A declined finding whose commit landed anyway is overridden: the decline said "this is not agreed" and the change went in regardless, which is exactly what open mode permits and exactly what a reviewer should see. A referral is never graded, because it is not an answer.

The revert window is policy, default one day:

```json
"outcomes": {"revert_window_days": 1}
```

The public census found that agent work that gets reverted is reverted within about a day, so the loop is fast. A revert after the window is recorded on the note and does not change the grade.

## Where the grade goes

- **Its own notes ref**, `refs/notes/gitvow/outcomes`, one note per commit, first line `gitvow-outcome`. Appended beside the decision, never rewriting the session note or the trailer. Pushed with every other gitvow ref by the pre-push hook. See the [note reference](../reference/note.md).
- **`gitvow show <commit>`** prints it after the session note.
- **`gitvow report`** and the pull request comment gain an **Outcome** line per commit.
- **`gitvow digest`** gains one line, counts only, never a person:

```
Outcomes: 12 decisions graded on 9 commits · 10 held · 1 did not hold · 1 overridden · 0 pending · revert window 1d · 3 landed without a pull request
```

- **The record server** answers `record_outcomes`.

Grading is idempotent. A grade nothing can change any more (a closed pull request, or a landing whose revert window has passed) is kept rather than recomputed; `--regrade` recomputes everything.

## What "landed without a pull request" means

On our own repositories the first run graded every decision `direct`: the commits went to `main` with no pull request at all. The grade is honest about that, because a decision that was never reviewed by a second person is a different fact from one that was merged after review, and the digest line says how many there were.

## What it is not

- Not attribution across an organisation. That is the store's business, with priors from the census. This grades one repository from its own history and its own forge.
- Not incidents or rollbacks. Those need deploy markers and an incident source, which the runtime facts provider and the blueprint bring later.
- Not a judgment of a person. The note carries the answer and the finding, which are already on the commit, and every count is per class of finding.
