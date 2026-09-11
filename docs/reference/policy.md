# Policy schema

```json
{
  "_doc": "optional free text",
  "bash_deny":    [{"pattern": "<regex>", "reason": "<text>"}],
  "bash_confirm": [{"pattern": "<regex>", "reason": "<text>", "when": "immediate"}],
  "path_confirm": [{"pattern": "<regex>", "reason": "<text>", "when": "commit"}],
  "mcp_allow":    ["<regex matched against the full tool name>"],
  "mcp_deny":     ["<regex matched against the full tool name>"],
  "providers": [{"name": "<text>", "command": "<program and arguments>", "questions": ["gate_bearing", "route_gate", "route_callers"], "timeout": 10}],
  "llm_classifier": {"enabled": false, "command": "<program and arguments, run without a shell>"},
  "snapshots": {"enabled": true, "max_per_session": 200, "exclude": [".env*", "*.pem", "*.key", "*secret*", "*credential*", ".gitvow/**", ".claude/**"]},
  "pricing": {"<model name or prefix>": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}},
  "decisions": {"mode": "open", "authorities": [], "production_branches": ["main", "master", "production"], "rule_threshold": 3, "rule_decay_days": 90}
}
```

| Key | Applies to | Match | Outcome |
|---|---|---|---|
| `bash_deny` | Bash command text | `re.search` | deny |
| `bash_confirm` | Bash command text | `re.search` | confirm; `when` defaults to `immediate` |
| `path_confirm` | file path of Edit, Write, MultiEdit, NotebookEdit | `re.search` | confirm; `when` defaults to `commit` |
| `mcp_deny` | MCP tool name | `re.fullmatch` | deny |
| `mcp_allow` | MCP tool name | `re.fullmatch`; if the list is non-empty, non-matching tools → confirm | |
| `providers` | Edit/Write paths and route literals in Edit text | each provider asked its declared questions; see [Provider protocol](provider-protocol.md) | `yes` → confirm at commit with evidence; failure → confirm immediately |
| `pricing` | note, ledger, report, digest | not a gate rule; USD per million tokens by model, overriding the built-in table | |
| `snapshots` | after each agent edit | not a gate rule; controls working-tree snapshots, see [Snapshots](../concepts/snapshots.md) | |
| `llm_classifier` | anything not decided above | command prints `ALLOW` / `CONFIRM reason` / `DENY reason` | as printed, immediately; failure → confirm |
| `decisions` | the card and the git hooks | not a gate rule; `mode` `open` (default) or `strict` for commits made by a person while findings are open; `authorities` names whose decisions count and who alone may accept a proposed rule, matched against committer email, its local part or name; `production_branches` where scoped decisions are reopened by the report; `rule_threshold` consistent **unscoped** answers by authorities before a finding is *proposed* as a rule, and the number of answers dated after a rejection before it is proposed again; `rule_decay_days` days without a new confirmation before a rule lapses | |

### `when`
`"when": "immediate"` refuses the call and asks now. `"when": "commit"` records a finding and lets the call run; the finding is put to a person when the agent commits, see [Decisions](../concepts/decisions.md). Deny rules have no `when`; a denial is always immediate. The default policy keeps edits to gitvow's own policy and hooks immediate, so a loosened policy can never take effect before a person has seen it.

Evaluation order: deny, confirm, MCP lists, providers, classifier, allow. Missing keys are treated as empty lists. Every pattern is compiled on load; a compile error or unreadable file makes the gate fail closed.

No key grants a rule. Reaching `rule_threshold` proposes one; a person named under `decisions.authorities` accepts it with `gitvow rules accept`, and that acceptance is a trailer on a commit rather than a line in this file, so no edit here can give a repository rules nobody agreed to. A scoped answer is an exception and never counts towards the threshold. See [Decisions](../concepts/decisions.md).

Lookup order: `<repo>/.gitvow/policy.json`, `~/.gitvow/policy.json`, package default.
