# gitvow

**Who made this change, what were they trying to do, and was it allowed?** For code written with AI agents, git alone cannot answer. gitvow makes it answer, using only git and a few hooks.

- Every commit made during an agent session carries the session id and a step number as **commit trailers**.
- Every such commit gets a **session note**: what the agent said it was doing, which tools it used, which files it touched, how much of the commit it wrote. Redacted before it is written. Stored as a git note, never in the tree.
- Every tool call passes a **gate** before it runs: destructive commands are refused, risky ones require the agent to ask you first, and edits to gate-bearing files need a human. The rules are a JSON file you own.
- The full session summary goes to a **local ledger** in your home directory. Nothing leaves the machine unless you push it.

No runtime dependencies. Standard-library Python and git. One command to install, one to remove.

```sh
pip install gitvow
gitvow install --user
gitvow selftest
```

## Why git, and why not a service

The provenance of a change should live where the change lives and be readable with the tools every engineer already has. `git log` shows which commits an agent made. `git log --show-notes=sessions` shows what it was thinking. A pull request diff is still the diff. Nothing new to log into, nothing that stops working when a vendor does.

## What gitvow is not

It is not a transcript store. Transcripts contain tool output, and tool output contains whatever the agent read. gitvow records structure and redacted intent, and leaves the content on the machine that produced it. See [What stays out of git](concepts/storage.md).

It is not a review tool, a chat UI or a hosted product. It is the layer those things should be built on.

## Supported agents

Claude Code today, through its hook system. The hook payload format is documented under [Reference](reference/hooks.md); adapters for other agents are welcome.
