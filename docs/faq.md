# FAQ

**Does gitvow send anything anywhere?** No. No network calls, no telemetry. Notes leave only when you push the ref.

**Will my teammates see the trailers?** Yes, they are part of the commit message. That is the point: `git log` shows which commits an agent made.

**Will they see the notes?** Only if the notes ref is pushed and they fetch it. GitHub's UI does not show notes.

**Can an agent turn the gate off?** Editing the policy or the hook settings requires confirmation under the default policy, and a missing policy fails closed. A determined human can, of course; the gate is for agents.

**What happens if a hook crashes?** Claude Code treats exit codes other than 0 and 2 as non-blocking errors and continues. gitvow only exits 2 deliberately; a crash in the policy path is caught and turned into a block.

**Why not store the transcript?** Because it contains tool output, and tool output contains whatever the agent read. See [What stays out of git](concepts/storage.md).

**Does it work with rebase and squash?** Trailers survive; each squashed commit's trailers are concatenated into the new message by git's default behaviour. Notes attach to the original commit objects and do not follow a rewrite unless `notes.rewriteRef` is configured; that is on the roadmap.

**Which agents?** Claude Code now. The payload shape is documented; adapters are welcome.
