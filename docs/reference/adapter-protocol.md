# External adapter protocol

The six built-in adapters are Python inside gitvow. Any other agent can be supported without changing gitvow: put an executable named `gitvow-agent-<name>` on the PATH, and `gitvow hook --agent <name>`, `gitvow install --agent <name>` and `gitvow selftest --agent <name>` will use it. gitvow never imports the executable; it runs it with JSON on stdin and reads JSON from stdout, the same discipline as providers.

## Discovery
`gitvow-agent-<name>` is looked up on the PATH, then in `~/.gitvow/agents/`. A built-in name always wins, so an external adapter cannot shadow Claude Code, Codex, Gemini, Cursor, Copilot or Factory.

## Subcommands

| Subcommand | stdin | stdout | Purpose |
|---|---|---|---|
| `info` | none | `{"protocol": 1, "name": "...", "events": ["..."], "install": {...}}` | declare the agent's hook events and where its configuration lives |
| `normalize` | `{"event": "<agent event>", "payload": {...}}` | `{"calls": [{"event": "PreToolUse", "payload": {...}}, ...]}` | translate one agent payload into zero or more gitvow hook calls in the [Claude Code shape](hooks.md) |
| `respond` | `{"code": 0 or 2, "message": "..."}` | `{"exit_code": n, "stdout": "...", "stderr": "..."}` | render gitvow's decision in the agent's native form |

`normalize` may return an empty `calls` list for events the adapter does not use. A `message` beginning `BLOCKED` is a denial; any other non-empty message on code 2 is a confirmation the agent should turn into its own "ask" if it has one.

## The `install` block of `info`

```json
"install": {
  "user_file": ".myagent/hooks.json",
  "repo_file": ".myagent/hooks.json",
  "format": "json",
  "entries": {"beforeTool": [{"command": "__GITVOW__ hook --agent myagent beforeTool"}]},
  "version_key": null
}
```

`gitvow install --agent <name>` merges `entries` into that file under a top-level `hooks` key, replacing `__GITVOW__` with the command to run gitvow, and `uninstall` removes exactly those entries again. Entries are recognised by the `gitvow hook ` marker in any `command` or `bash` field. `version_key` names a top-level key to set to `1` when the file is created, for agents that require it.

## Failure behaviour
If the executable is missing, exits non-zero, times out (10 s) or returns malformed JSON during a **PreToolUse**, gitvow blocks the call with the error, exactly as a missing policy does. During other events it logs `adapter_failed` and continues, so a broken adapter can stop an edit but cannot lose a session's record.

## A complete example
`examples/agents/gitvow-agent-example` in the repository is a working adapter in about sixty lines of Python for a fictional agent whose hooks are called `beforeTool` and `afterTool` and whose payload uses `sid`, `tool` and `args`. It is what the tests run. Copy it, rename it, change the three functions.
