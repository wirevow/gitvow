# Policy schema

```json
{
  "_doc": "optional free text",
  "bash_deny":    [{"pattern": "<regex>", "reason": "<text>"},
                   {"program": "kubectl", "verbs": ["delete", "drain"], "reason": "<text>"}],
  "bash_confirm": [{"pattern": "<regex>", "reason": "<text>", "when": "immediate"}],
  "path_confirm": [{"pattern": "<regex>", "reason": "<text>", "when": "commit"}],
  "mcp_allow":    ["<regex matched against the full tool name>"],
  "mcp_deny":     ["<regex matched against the full tool name>"],
  "providers": [{"name": "<text>", "command": "<program and arguments>", "questions": ["gate_bearing", "route_gate", "route_callers"], "timeout": 10}],
  "llm_classifier": {"enabled": false, "command": "<program and arguments, run without a shell>"},
  "snapshots": {"enabled": true, "max_per_session": 200, "exclude": [".env*", "*.pem", "*.key", "*secret*", "*credential*", ".gitvow/**", ".claude/**"]},
  "pricing": {"<model name or prefix>": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}},
  "decisions": {"mode": "open", "session_scope": true, "authorities": [], "production_branches": ["main", "master", "production"], "rule_threshold": 3, "rule_decay_days": 90}
}
```

| Key | Applies to | Match | Outcome |
|---|---|---|---|
| `bash_deny` | Bash command text | `re.search` on `pattern`, or on the regex built from `program` and `verbs` | deny |
| `bash_confirm` | Bash command text | as above | confirm; `when` defaults to `immediate` |
| `path_confirm` | file path of Edit, Write, MultiEdit, NotebookEdit | `re.search` | confirm; `when` defaults to `commit` |
| `mcp_deny` | MCP tool name | `re.fullmatch` | deny |
| `mcp_allow` | MCP tool name | `re.fullmatch`; if the list is non-empty, non-matching tools → confirm | |
| `providers` | Edit/Write paths and route literals in Edit text | each provider asked its declared questions; see [Provider protocol](provider-protocol.md) | `yes` → confirm at commit with evidence; failure → confirm immediately |
| `pricing` | note, ledger, report, digest | not a gate rule; USD per million tokens by model, overriding the built-in table | |
| `snapshots` | after each agent edit | not a gate rule; controls working-tree snapshots, see [Snapshots](../concepts/snapshots.md) | |
| `llm_classifier` | anything not decided above | command prints `ALLOW` / `CONFIRM reason` / `DENY reason` | as printed, immediately; failure → confirm |
| `decisions` | the card and the git hooks | not a gate rule; `mode` `open` (default: the agent's commit is stopped once with the card, then goes through with `Gitvow-Open` for every unanswered finding; a person's commit goes through the same way) or `strict` (agent and person alike refused until every finding is decided); `authorities` names whose decisions count and who alone may accept a proposed rule, matched against committer email, its local part or name; `production_branches` where scoped decisions are reopened by the report; `rule_threshold` consistent **unscoped** answers by authorities before a finding is *proposed* as a rule, and the number of answers dated after a rejection before it is proposed again; `rule_decay_days` days without a new confirmation before a rule lapses; `session_scope` (default `true`) records an immediate confirm as a finding and lets one answer hold for the session, `false` asks every time and records nothing | |

### `program` and `verbs`
A command rule may name a `program` (or a list of them) and the `verbs` that make it consequential, instead of a hand-written regex. gitvow builds the regex and allows options between the two: `kubectl --context prod delete pod x` is a `kubectl delete`. A hand-written `\bkubectl\s+delete` is not, and until 0.17.1 the default policy was written that way; replaying three engineers' real sessions found 163 cluster mutations that had walked past it on a `--context` flag. Each verb is a regex fragment, so multi-word verbs are written `pr\s+merge`. The finding a `program` rule raises is `run <program> (<reason>)`, so the program name is what precedent attaches to. Write new command rules in this form; keep `pattern` for shapes it cannot express, such as the database-write rule, which needs a client program and a statement anywhere after it.

### `when`
`"when": "immediate"` refuses the call and asks now; since 0.18 the finding is recorded with a number and a person's answer holds for the session (see `decisions.session_scope`). `"when": "commit"` records a finding and lets the call run; the finding is put to a person when the agent commits, see [Decisions](../concepts/decisions.md). `"when": "observe"` records the finding and lets the call run, and nobody is ever asked: it rides on the commit as `Gitvow-Observed`, is counted by the digest, and is never precedent. Deny rules have no `when`; a denial is always immediate. The default policy keeps edits to gitvow's own policy and hooks immediate, so a loosened policy can never take effect before a person has seen it.

Evaluation order: deny, confirm, MCP lists, providers, classifier, allow. Missing keys are treated as empty lists. Every pattern is compiled on load; a compile error or unreadable file makes the gate fail closed.

No key grants a rule. Reaching `rule_threshold` proposes one; a person named under `decisions.authorities` accepts it with `gitvow rules accept`, and that acceptance is a trailer on a commit rather than a line in this file, so no edit here can give a repository rules nobody agreed to. A scoped answer is an exception and never counts towards the threshold. See [Decisions](../concepts/decisions.md).

Lookup order: `<repo>/.gitvow/policy.json`, `~/.gitvow/policy.json`, package default.
