# Let the agent ask the record

Everything gitvow records is meant to be consulted by the next session, not only by a reviewer. Four commands answer the questions an agent most often needs answered, and four matching **skills** teach Claude Code, Codex, Cursor and Gemini CLI to call them.

## The four questions

| Command | Question it answers | Reads |
|---|---|---|
| `gitvow why <path>` | Why does this file look the way it does? Which sessions shaped it, what did each intend, how much did a person change afterwards? | trailers, session notes, snapshots |
| `gitvow trace <path>[:<start>-<end>]` | Who wrote these lines, agent or person, in which session and step, with what stated plan? | `git blame`, trailers, notes |
| `gitvow recall <words>` | Has this been worked on before? Which sessions mention these terms? | session notes across all sessions, the ledger |
| `gitvow handoff [--session <id>]` | What should the next agent know to continue? | ledger, notes, snapshots, uncommitted state |

All four are read-only, local, and need nothing but git and the files gitvow already writes.

## Examples

```
$ gitvow why src/api/OrdersResource.java
src/api/OrdersResource.java · 3 agent commits, 1 human commit, 2 sessions

2026-09-09  3b7a97b  session 8f3d5c71 step 4   agent share 1.00
  Change the cache key to include week start so per-org settings do not collide.
2026-09-08  0977d2f  session a28bd848 step 1   agent share 0.50 · 4 lines changed by a person afterwards
  Add the export endpoint behind the standard filter.
2026-09-07  6bf15f7  (person)
```

```
$ gitvow trace src/api/OrdersResource.java:40-60
lines 40-52  agent   3b7a97b  session 8f3d5c71 step 4  "Change the cache key to include week start..."
lines 53-60  person  6bf15f7
```

```
$ gitvow recall week start cache
session 8f3d5c71  2026-09-09  2 commits  "Change the cache key to include week start so per-org settings do not collide"
session c3d9e0aa  2026-09-02  1 commit   "Investigate stale week totals; cache key ignores org week start"
```

```
$ gitvow handoff
# Handoff · session 8f3d5c71 · repo gitvow
Last stated plan: Change the cache key to include week start ...
Commits this session: 3b7a97b Fix week-start cache key
Files the agent wrote: query-engine/.../QueryCacheHelper.java
Uncommitted now: 1 file changed vs HEAD · last snapshot refs/gitvow/snapshots/8f3d5c71.../7
Open confirmations: 1 (kubectl apply -f x.yaml, cluster mutation)
```

## Install the skills

The skills live in [wirevow/gitvow-skills](https://github.com/wirevow/gitvow-skills) in the common Agent Skills format, one `SKILL.md` per skill, and install with the skills tooling every supported agent reads:

```sh
npx skills add https://github.com/wirevow/gitvow-skills --all
```

Claude Code users can also add the repository as a plugin marketplace. Each skill tells the agent when to reach for the record and how to read the output; the agent does the rest with the commands above. Skills need `gitvow` on the agent's PATH, which `gitvow install` already ensures for the per-user install.

| Skill | Use it when |
|---|---|
| `gitvow-why` | before changing code you did not write, or when a reviewer asks why something exists |
| `gitvow-trace` | when a code region looks surprising and you need its origin, agent or person |
| `gitvow-recall` | before starting work, to find earlier sessions on the same feature or bug |
| `gitvow-handoff` | at the end of a session, or when switching agents |

## What they do not do
They do not read transcripts, which stay local by design; everything they say comes from the redacted record. They do not summarise across repositories. They answer from what was recorded; a repository with no sessions yet gives short answers.
