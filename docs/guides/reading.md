# Read a commit's session

```sh
gitvow show HEAD
```

prints the commit's subject and body, including trailers, followed by its session note. It reads the session id from the trailer and looks up `refs/notes/gitvow/<session-id>`. Equivalent git commands, once `gitvow install` has set `notes.displayRef`:

```sh
git log --show-notes -1
git notes --ref=gitvow/<session-id> show <commit>
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
 "attribution": {
  "files_in_commit": 1, "touched_by_agent": 1,
  "lines_added_in_commit": 3,
  "lines_changed_by_human_after_agent": 1,
  "agent_share": 0.67,
  "files": [{"path": "query-engine/.../QueryCacheHelper.java", "agent_wrote": true,
             "lines_added_in_commit": 3, "human_lines_added": 1, "human_lines_removed": 0}]
 }
}
```

Read it in this order. **The plan** tells you what the agent thought it was doing; compare it to the diff. **Attribution** tells you who wrote what: `agent_share` is the fraction of lines added in the commit that match the agent's last written version of each file, and the per-file human counts say how much a person changed after the agent. A share of 1.0 means the commit is exactly what the agent produced; a low share on a file the agent touched means a person reworked it before committing. **Step and turns** tell you how deep into a session the change was made; a step 12 commit after 300 tool calls deserves a closer look than a step 1 commit after ten.

Attribution works by recording the blob id of each file right after the agent writes it, then diffing that blob against the committed one. It is exact for what the agent wrote through its editing tools; edits the agent makes through shell commands are not attributed to it.

## In a pull request
GitHub does not display git notes. The [gitvow action](pull-requests.md) posts every session note on the pull request as a comment and job summary. Locally: `git fetch origin 'refs/notes/gitvow/*:refs/notes/gitvow/*'` then `gitvow show <sha>`.

## Finding sessions
```sh
git log --grep=Gitvow-Session:                          # all agent commits
git log --grep='Gitvow-Session: <id>' --show-notes
grep -h session_id ~/.gitvow/ledger/*.json | sort | uniq -c
```
