# Policy schema

```json
{
  "_doc": "optional free text",
  "bash_deny":    [{"pattern": "<regex>", "reason": "<text>"}],
  "bash_confirm": [{"pattern": "<regex>", "reason": "<text>"}],
  "path_confirm": [{"pattern": "<regex>", "reason": "<text>"}],
  "mcp_allow":    ["<regex matched against the full tool name>"],
  "mcp_deny":     ["<regex matched against the full tool name>"],
  "llm_classifier": {"enabled": false, "command": "<program and arguments, run without a shell>"}
}
```

| Key | Applies to | Match | Outcome |
|---|---|---|---|
| `bash_deny` | Bash command text | `re.search` | deny |
| `bash_confirm` | Bash command text | `re.search` | confirm |
| `path_confirm` | file path of Edit, Write, MultiEdit, NotebookEdit | `re.search` | confirm |
| `mcp_deny` | MCP tool name | `re.fullmatch` | deny |
| `mcp_allow` | MCP tool name | `re.fullmatch`; if the list is non-empty, non-matching tools → confirm | |
| `llm_classifier` | anything not decided above | command prints `ALLOW` / `CONFIRM reason` / `DENY reason` | as printed; failure → confirm |

Evaluation order: deny, confirm, MCP lists, classifier, allow. Missing keys are treated as empty lists. Every pattern is compiled on load; a compile error or unreadable file makes the gate fail closed.

Lookup order: `<repo>/.gitvow/policy.json`, `~/.gitvow/policy.json`, package default.
