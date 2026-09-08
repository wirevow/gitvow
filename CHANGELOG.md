# Changelog

## Unreleased

## 0.2.2 — 2026-09-09
- Fix: a commit made by a person in a terminal after (or alongside) a session received the session's trailer, because the session state outlived the session. Trailers are now added only while a `pending_commit` mark set by the gate on the agent's own `git commit` is fresh, and a note is written only when HEAD carries the trailer. Found in an end-to-end run with a real Claude Code session.

## 0.2.1 — 2026-09-09
- Per-user install records the absolute path of the `gitvow` executable in the hook command (falling back to `python -m gitvow`), so hooks run even when Claude Code's shell lacks the virtualenv or pipx PATH. Found in an end-to-end run with a real Claude Code session. Per-repo installs keep the bare name. New `python -m gitvow` entry point.

## 0.2.0 — 2026-09-09
- Session notes now live on one ref per session, `refs/notes/gitvow/<session-id>`; parallel agents never contend and pushing `refs/notes/gitvow/*` never conflicts. 0.1 notes on `refs/notes/sessions` are still read.
- Custom redaction rules from `<repo>/.gitvow/redact-rules.json` and `~/.gitvow/redact-rules.json`; both apply. An invalid rules file fails closed: nothing is written for that event.
- Line-level attribution in the note: per-file human lines changed after the agent's last write, and `agent_share` for the commit. PostToolUse now also runs on Edit, Write, MultiEdit and NotebookEdit to record the agent's written blob.
- `install` sets `notes.displayRef` and `notes.rewriteRef` to `refs/notes/gitvow/*` so `git log --show-notes` shows notes and they follow amend, rebase and squash; `uninstall` removes the keys.
- New `gitvow redact <text>` command to test rules. Note schema field `schema: 2`.

## 0.1.2 — 2026-09-08
- Fix: `gitvow --version` reported a hardcoded 0.1.0; the version now comes from package metadata, and the packaging test asserts it matches `pyproject.toml`.

## 0.1.1 — 2026-09-08
- Fix: `default_policy.json` was missing from the wheel, so a fresh install failed closed and blocked every tool call. Now shipped as package data, with a test that installs the built wheel and runs the self-check.

## 0.1.0 — 2026-09-08
- Session trailers (`Gitvow-Session`, `Gitvow-Step`) on commits made during Claude Code sessions.
- Redacted structural session notes under `refs/notes/sessions`, with per-file agent attribution.
- Tool-call gate: deny / confirm / allow on Bash commands, edited paths and MCP tool names; optional classifier; fails closed.
- Local ledger, audit log, `collect` and `summarize` for trials, `check`, `show`, `selftest`.
- Per-user and per-repo install, idempotent and fully reversible.
