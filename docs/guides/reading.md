# Read a commit's session

```sh
gitvow show HEAD
```

prints the commit's subject and body, including trailers, followed by its session note. Equivalent git commands:

```sh
git log --show-notes=sessions -1
git notes --ref=sessions show <commit>
```

## Reading a note
```
gitvow-session
{
 "session_id": "8f3d5c71-...",
 "step": 4,
 "assistant_turns_so_far": 41,
 "tool_calls_so_far": 118,
 "tools_used": ["Bash", "Edit", "Read"],
 "last_stated_plan": "Change the cache key to include week start so per-org week settings do not collide...",
 "files_in_commit": ["query-engine/.../QueryCacheHelper.java | 4 +++-"],
 "files_written_by_agent_this_session": ["query-engine/.../QueryCacheHelper.java"],
 "attribution": {"files_in_commit": 1, "touched_by_agent": 1}
}
```

Read it in this order. **The plan** tells you what the agent thought it was doing; compare it to the diff. **Attribution** tells you whether the agent wrote what is in the commit or a person did after it. **Step and turns** tell you how deep into a session the change was made; a step 12 commit after 300 tool calls deserves a closer look than a step 1 commit after ten.

## In a pull request
GitHub does not display git notes. Until a check publishes them, the fastest path is `git fetch origin refs/notes/sessions:refs/notes/sessions` then `gitvow show <sha>` locally, or ask the author to paste `gitvow show` output into the description.

## Finding sessions
```sh
git log --grep=Gitvow-Session:                          # all agent commits
git log --grep='Gitvow-Session: <id>' --show-notes=sessions
grep -h session_id ~/.gitvow/ledger/*.json | sort | uniq -c
```
