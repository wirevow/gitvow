# Architecture

```
src/gitvow/
  cli.py          argument parsing; every subcommand is one function; exit codes 0/1/2
  hooks/          the four Claude Code hook handlers; pure functions (payload, home) → (exit code, stderr text)
  policy.py       load (with precedence + validation) and evaluate; Decision(outcome, reason, detail)
  redact.py       layered redaction; the only place text is scrubbed
  transcript.py   read Claude Code JSONL; produce counts, tool list, files written, last plan
  state.py        git helpers; .git/gitvow-session.json; .git/gitvow-hooks.log
  install.py      settings merge/unmerge; git hook script; user/repo install and uninstall
  collect.py      gather ledger + logs + trailers + notes for a trial; summarize
  selftest.py     end-to-end self-check in a temporary repo and home
  default_policy.json
```

Invariants the tests enforce:
- Hooks never raise to the caller; any exception in the decision path becomes exit 2.
- Nothing from `transcript.py` reaches `state.py`, `hooks/` or `collect.py` without passing `redact.redact`.
- `install` twice equals `install` once; `install` then `uninstall` restores the settings file byte-for-byte except gitvow's entries.
- The package imports only the standard library.
