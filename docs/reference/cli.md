# CLI

```
provkit hook <SessionStart|PreToolUse|PostToolUse|Stop>   run as a Claude Code hook; reads the JSON payload on stdin; exits 0 to allow, 2 to block
provkit install --user | install [repo]                    install per user or into a repository (default: current directory)
provkit uninstall --user [--purge-policy] [--purge-ledger] remove the user install
provkit uninstall [repo] [--purge-notes]                   remove a repository install
provkit check -- <command...>                              dry-run the policy against a Bash command; exit 0 allow, 2 blocked
provkit check --path <file>                                dry-run against an edited path
provkit check --mcp <tool-name>                            dry-run against an MCP tool name
provkit show [commit]                                      print a commit's message, trailers and session note (default HEAD)
provkit collect [--out DIR]                                gather ledger, logs, trailers and notes into one redacted directory (default ~/Desktop)
provkit summarize <dir>                                    trial metrics from a collected directory
provkit selftest                                           drive every hook in a throwaway repository and report pass/fail
provkit --version
```

Exit codes: `0` success or allow; `2` blocked (hook and check); `1` error.

Environment: none required. `HOME` decides where the user policy and ledger live. Tests set `GIT_CONFIG_GLOBAL` to isolate git configuration.
