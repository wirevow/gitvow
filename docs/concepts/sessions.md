# Sessions, steps and notes

## Session
One Claude Code conversation, identified by the session id Claude Code assigns. gitvow records it in the repository's `.git` directory when the session starts, so git hooks can see it. It never enters the working tree.

## Step
A counted commit within a session. The first commit an agent makes in a session is step 1, the next step 2. The number lets you order a session's commits without looking at timestamps, and lets a reviewer see how far into a session a change was made.

## Trailers
Two lines appended to the commit message by a `prepare-commit-msg` git hook:

```
Gitvow-Session: 8f3d5c71-574a-4eec-8903-9425e3a8335b
Gitvow-Step: 4
```

Trailers survive rebase, amend, squash and cherry-pick because they are part of the message rather than derived from the commit hash. Commits made outside a session get none, so the absence of a trailer is itself information.

Useful queries:

```sh
git log --grep=Gitvow-Session:                 # every agent-made commit
git log --grep='Gitvow-Session: 8f3d5c71'      # everything one session produced
```

## Session note
A structural summary attached to the commit as a git note under `refs/notes/gitvow/<session-id>`:

- the session id and step
- how many assistant turns and tool calls had happened so far
- which tools were used
- the agent's last stated plan before committing, after redaction
- the files in the commit, and which of them the agent itself wrote or edited in this session
- line-level attribution: for each file the agent wrote, how many lines a human changed after the agent's last write, and the agent's share of the lines added in the commit

Notes are git objects but not part of the tree. `git status`, diffs and checkouts never see them. They are local until pushed, and read with `gitvow show <commit>` or plain `git log --show-notes` once installed.

### One ref per session
Each session writes only to its own ref. Two agents committing in the same repository at the same time never touch the same ref, and pushing notes never conflicts, because no two machines ever write the same ref:

```sh
git push origin 'refs/notes/gitvow/*'          # publish every session's notes
git fetch origin 'refs/notes/gitvow/*:refs/notes/gitvow/*'
```

Notes written by gitvow 0.1 live on the single ref `refs/notes/sessions`; `gitvow show` still reads them. The per-session refs use a different prefix because git cannot hold a ref and a directory of refs under the same name.

### Notes follow rewrites
Install sets `notes.rewriteRef` to the session refs, so `git commit --amend`, `git rebase` and squash merges carry each note to the rewritten commit. Trailers are in the message and survive on their own. A squash of several agent commits concatenates their trailers and keeps the notes of every squashed commit on the result.

## Ledger
At the end of a session, a redacted summary is written to `~/.gitvow/ledger/<session-id>.json`: every tool call with a shortened argument, the commits made during the session, the last stated plan. It is the fullest record gitvow keeps, and it never enters a repository. See [What stays out of git](storage.md).
