# A period in one page

`gitvow report` describes a pull request. `gitvow digest` describes a stretch of time: what the agents did in this repository over the last week, how much of it a person changed, and what the gate stopped. It is the page an engineering lead reads on Monday, and it comes entirely from the record.

```sh
gitvow digest                      # last 7 days on the current branch
gitvow digest --since 30d          # or 2026-09-01
gitvow digest --since 14d --json   # machine-readable
```

## What it shows

```
## gitvow digest · gitvow · 2026-09-02 → 2026-09-09

Commits: 41 · by agents 29 (71%) · by people 12
Sessions: 9 · agent share of added lines 0.83 · lines changed by people after agents 212
Gate: 6 confirmations asked, 2 denials · top reasons: pushing to a remote (4), force push (2)

### Sessions
8f3d5c71  09-09  4 commits  share 1.00  "Change the cache key to include week start so per-org settings do not collide"
a28bd848  09-08  1 commit   share 0.50  "Add the export endpoint behind the standard filter"  · 2 human edits after
...

### Files most changed by agents
query-engine/.../QueryCacheHelper.java  3 commits, 2 sessions
src/api/OrdersResource.java             2 commits, 1 session

### Human commits in the period
6bf15f7  09-07  human adds helper
```

Every line comes from trailers, session notes, and the hook log in `.git`. Agent share and human-edit counts are the same numbers the session notes carry; the digest only adds them up. Gate counts come from the repository's own hook log, so they cover sessions run on this machine.

## What it is for
- A weekly read of how much of the repository's change is agent-made and how much of that people reworked, which is the closest thing to a trust curve the record offers.
- Spotting sessions with a low agent share or many human edits afterwards: the agent's work needed correction, and the plan says what it was attempting.
- Seeing which gate rules fire in practice, before tightening or relaxing the policy.

## Limits
It reads the current branch's history. Sessions whose notes were never pushed appear with commits but without plans. It does not read transcripts.
