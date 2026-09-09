# FAQ

**Does gitvow send anything anywhere?** No. No network calls, no telemetry. Notes leave only when you push the ref.

**I committed by hand while the agent was working. Does my commit get a trailer?** No. Trailers go only on commits the agent runs through its tools. Yours has none, and no note.

**Will my teammates see the trailers?** Yes, they are part of the commit message. That is the point: `git log` shows which commits an agent made.

**Will they see the notes?** On the pull request, yes, through the [gitvow action](guides/pull-requests.md). Notes are pushed automatically by the pre-push hook; GitHub's own UI does not show git notes.

**Can an agent turn the gate off?** Editing the policy or the hook settings requires confirmation under the default policy, and a missing policy fails closed. A determined human can, of course; the gate is for agents.

**What happens if a hook crashes?** Claude Code treats exit codes other than 0 and 2 as non-blocking errors and continues. gitvow only exits 2 deliberately; a crash in the policy path is caught and turned into a block. The one failure this does not cover is a hook command that cannot be found at all, which is why a per-user install records the absolute path of the executable; see [Install](guides/install.md).

**Can I get back what the agent had before I changed it?** Yes. Every agent edit takes a snapshot under a session ref; `gitvow snapshots`, `gitvow diff` and `gitvow restore` list, compare and check one out into a scratch worktree. Code only; the conversation stays local. See [Snapshots](concepts/snapshots.md).

**Why not store the transcript?** Because it contains tool output, and tool output contains whatever the agent read. See [What stays out of git](concepts/storage.md).

**Does it work with rebase and squash?** Yes. Trailers are in the message and survive on their own. Install sets `notes.rewriteRef` so notes follow amend, rebase and squash to the rewritten commit.

**Two agents in one repository at once?** Each session writes its own notes ref, `refs/notes/gitvow/<session-id>`, so they never contend, and pushing notes from many machines never conflicts.

**Which agents?** Claude Code, verified in real sessions. Codex CLI, Gemini CLI and Cursor through adapters built against their documented hook contracts; see [Codex, Gemini and Cursor](guides/other-agents.md).
