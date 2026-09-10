# The gate

Before your agent runs a tool, it asks gitvow. gitvow answers in one of three ways. The same policy and the same answers apply to every [supported agent](../guides/other-agents.md); only the way a refusal is delivered differs.

| Outcome | What happens | Example |
|---|---|---|
| **allow** | The call runs. It is logged. | `ls -la`, `git commit`, `pytest` |
| **confirm** | The call is refused with an instruction to ask you first. | `git push`, `kubectl apply`, editing a production values file |
| **deny** | The call is refused with the reason. | `rm -rf /`, `git push --force`, `terraform destroy` |

The decision is made from a policy file of regular expressions, evaluated in the order deny, confirm, allow. Anything the policy does not mention is allowed and logged, so the log tells you what your policy has never considered.

## Two moments to confirm
A confirm rule is either **immediate** or **at commit**. Immediate rules stop the call at once: pushes, cluster and infrastructure mutations, anything that cannot be undone. At-commit rules let the agent keep working and collect the finding; when the agent runs `git commit`, every open finding is put to a person on one card, and the answers are written into the commit. Bash rules default to immediate, path and provider rules to commit. See [Decisions](decisions.md).

## What the gate looks at
- **Bash**: the full command text.
- **Edit, Write, MultiEdit, NotebookEdit**: the file path.
- **MCP tools**: the tool name, `mcp__<server>__<tool>`, against a deny list and an optional allow list.
- **Providers**: optional programs that answer factual questions about the edit, such as whether a file is gate-bearing or a new route would be exposed without authorization. See [Providers](providers.md).
- **Anything else**: an optional classifier command you provide, for decisions regular expressions cannot make.

## Fail closed
If the policy file is missing or invalid, every tool call is refused until it is restored. A gate that fails open is not a gate.

## Where the policy comes from
`<repo>/.gitvow/policy.json` if present, else `~/.gitvow/policy.json`, else the package default. A repository can therefore tighten or override a user's defaults, and the repository's policy can be reviewed in a pull request like any other change.

## What the gate cannot see
It inspects tool calls the agent makes through its hooks. A Bash command is inspected as text; a process it spawns is not seen separately. Path checks use the path, not the diff. Regular expressions are a floor; providers and the classifier are how you raise it. See [Write a policy](../guides/policy.md).
