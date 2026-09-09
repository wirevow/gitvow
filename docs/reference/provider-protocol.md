# Provider protocol

A provider is a command. gitvow runs it once per question with a JSON object on stdin and expects one JSON object on stdout. No shell is involved: the policy names a program and its arguments.

## Request

```json
{
  "protocol": 1,
  "question": "route_gate",
  "subject": "/v1/orders/export",
  "repo": "/abs/path/to/repo",
  "path": "src/api/OrdersResource.java",
  "tool_name": "Edit"
}
```

| Field | Meaning |
|---|---|
| `protocol` | protocol version, currently 1 |
| `question` | `gate_bearing`, `route_gate` or `route_callers` |
| `subject` | the file path (repo-relative) for `gate_bearing`; the route literal for the route questions |
| `repo` | absolute path of the repository's top level |
| `path` | the file being edited, repo-relative |
| `tool_name` | the tool the agent is about to run |

## Response

```json
{"answer": "yes", "evidence": ["not covered by an authorization filter", "whitelist: auth/AuthorizeWhitelistedPaths.java"], "confidence": 0.9}
```

| Field | Required | Meaning |
|---|---|---|
| `answer` | yes | `yes`, `no` or `unknown` |
| `evidence` | no | short strings, shown verbatim to the agent; keep them free of secrets |
| `confidence` | no | 0 to 1, informational |

Exit code 0 with a valid object is the only success. Anything else is a provider failure, which the gate turns into confirm.

## Policy configuration

```json
"providers": [
  {
    "name": "topology",
    "command": "python3 /opt/gitvow/topology_provider.py --facts /var/lib/topology/facts.db",
    "questions": ["gate_bearing", "route_gate", "route_callers"],
    "timeout": 10
  }
]
```

| Key | Default | Meaning |
|---|---|---|
| `name` | required | shown in messages and logs |
| `command` | required | program and arguments, split like a shell would, run without one |
| `questions` | all three | which questions this provider answers |
| `timeout` | 10 | seconds before the provider counts as failed |

Several providers may be listed; each is asked the questions it declares, and any `yes` requires confirmation. Providers run after the regular-expression rules and before the classifier.

## Testing a provider

```sh
gitvow ask gate_bearing src/auth/Whitelist.java
gitvow ask route_gate /v1/orders/export --path src/api/Orders.java
```

prints each configured provider's answer and evidence, and the decision the gate would make.
