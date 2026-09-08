# Write a policy

The policy is a JSON file of regular expressions. Start from the default:

```sh
provkit install --user            # writes ~/.provkit/policy.json if absent
$EDITOR ~/.provkit/policy.json
provkit check -- <a command>      # dry-run any rule
```

## Shape

```json
{
  "bash_deny":    [ {"pattern": "\\bgit\\s+push\\b.*(--force|-f\\b)", "reason": "force push"} ],
  "bash_confirm": [ {"pattern": "\\bgit\\s+push\\b",                  "reason": "pushing to a remote"} ],
  "path_confirm": [ {"pattern": "(^|/)values/production-[a-z]+/.*\\.ya?ml$", "reason": "edits production values"} ],
  "mcp_allow":    [],
  "mcp_deny":     [ "mcp__.*__(delete|remove|destroy|drop)_.*" ],
  "llm_classifier": { "enabled": false, "command": "" }
}
```

Patterns are Python `re` syntax, searched (not anchored) against the Bash command text or the file path; MCP patterns are matched against the whole tool name.

## Advice from running it
- Put things you never want in `bash_deny`. Put things you want to hear about in `bash_confirm`. Confirm is cheap: it costs one question and the log shows how often it fires.
- Name the files that decide who can do what in `path_confirm`: authorization filters, allow-lists, production deployment values, CI definitions. The default includes provkit's own policy and the agent settings file, so an agent cannot quietly loosen the gate.
- Leave `mcp_allow` empty until you know which MCP servers your team uses; a non-empty allow list turns every other MCP tool into a confirmation.
- Watch the log for a week before tightening. Rules that never fire cost nothing; rules that fire on routine work will be resented and then removed.

## The classifier
For decisions regular expressions cannot express, "is this `sed -i` on a config file part of the task or an attempt to edit the gate", set `llm_classifier.enabled` to true and `command` to a program (split like a shell would, but run **without** a shell, so redirections and pipes are not available and policy text can never become shell syntax) that reads `{"tool_name":..., "tool_input":...}` on stdin and prints `ALLOW`, `CONFIRM <reason>` or `DENY <reason>`. It runs only for calls the regular expressions did not decide. If it fails or times out, the call requires confirmation.

## Validation
provkit validates the file on every load: every entry needs a `pattern`, every pattern must compile. An invalid file fails closed, refusing all tool calls with the reason, so a typo is loud rather than silent.
