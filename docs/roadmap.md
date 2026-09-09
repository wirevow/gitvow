# Roadmap

gitvow is deliberately small. These are the things it should grow into, in order, with the exit test for each.

## 0.2 — Trial-hardened (shipped)
- One notes ref per session, `refs/notes/gitvow/<session-id>`, so parallel agents never contend and pushes never conflict.
- Custom redaction rules from `.gitvow/redact-rules.json` in the repository and in the home directory; invalid rules fail closed.
- Line-level attribution: lines a human changed after the agent, per file, and the agent's share of the commit.
- Notes follow amend, rebase and squash through `notes.rewriteRef`.
- Exit still open: a five-person, two-week trial produces zero secrets in collected archives on manual review.

## 0.3 — Reviewer surface (shipped)
- `gitvow report` and a GitHub Action that post each pull request's session notes as one updated comment and as the job summary.
- The check fails when a commit carries a session trailer but its note was never pushed. (A commit without any trailer is, by construction, a human commit; nothing can prove otherwise, so the check reports it rather than failing.)
- "Said versus did": each changed file checked against the agent's stated plan.
- Notes pushed automatically by a `pre-push` hook; `gitvow push-notes` for by hand.
- Exit still open: two teams review with it on; time-to-first-comment measured.

## 0.4 — Policy from facts (shipped)
- Provider protocol: the gate asks external programs `gate_bearing`, `route_gate` and `route_callers`; a `yes` requires a human, with the provider's evidence in the message. Failure means confirm. gitvow defines the contract; providers live elsewhere; an example over a static facts file ships in the repository.
- Exit met: a real agent session adding an unauthorised route to a synthetic repository is stopped, and the message names the whitelist file.

## Later
- Adapters for other agents that expose before/after tool hooks.
- A configuration-driven storage tier for the ledger (restricted repository, object store).

## Not planned
Hosting, mirroring, cloud summaries of transcripts, a web UI. gitvow is the layer those things consume.
