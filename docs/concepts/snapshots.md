# Snapshots: the code as the agent left it

A commit records what was accepted. A snapshot records what the agent produced, edit by edit, whether or not anything was committed afterwards. Together they answer the question a commit alone cannot: *what did the working tree look like at step 7 of the session, before a person changed it?*

## What is recorded
After every Edit, Write, MultiEdit or NotebookEdit the agent performs, gitvow writes the working tree into a snapshot commit and records it under a ref that belongs to the session:

```
refs/gitvow/snapshots/<session-id>/<n>
```

- The snapshot's parent is the current HEAD, so `git diff HEAD <snapshot>` shows exactly what the agent had changed at that moment.
- Ignored files are excluded, and so is a default exclusion list (`.env*`, `*.pem`, `*.key`, `*secret*`, `*credential*`, and gitvow's own `.gitvow/` and `.claude/` directories), so a snapshot never contains more than a careful `git add -A` would. Excluded paths that are already committed stay as they are in HEAD.
- The ref is not under `refs/heads` or `refs/notes`, so it never appears in `git log`, is never pushed by the pre-push hook, and is invisible to branches. `git gc` keeps it because a ref points at it.
- The session note on a later commit names the last snapshot taken before that commit, which ties the two records together.

## What it is for
- **Rewind code, not conversation.** Restore a snapshot into a scratch worktree and read or run the agent's version. The transcript stays local by design; a snapshot is the code side of that story.
- **See what a person changed.** Line-level attribution already tells you how many lines a human changed after the agent; the snapshot lets you see which.
- **Recover discarded work.** An agent's edit that was reverted before committing is still in the snapshot.

## Retention and cost
Snapshots live in `.git/objects` and cost roughly what one `git add -A` costs per edit. gitvow keeps the most recent 200 per session by default and `gitvow snapshots prune --older-than 14d` deletes older refs; unreferenced objects go with the next `git gc`. `gitvow uninstall --purge-snapshots` deletes them all. Snapshots can be turned off in the policy file.

## What it does not do
It does not restore the conversation, the tool outputs or the agent's memory. It does not follow amend or rebase, because it is anchored to the commit that was HEAD at the time and that is the point. It does not snapshot edits the agent makes through shell commands, only through its editing tools, the same boundary as attribution.
