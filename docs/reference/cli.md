# CLI

```
gitvow hook [--agent claude|codex|gemini|cursor|copilot|factory] <Event>  run as an agent hook; reads the agent's JSON payload on stdin; answers in the agent's form
gitvow install --user | install [repo] [--agent NAME] [--check]
                                                          install per user or into a repository (default: current directory); with no --agent, configures every agent found on this machine; --check runs the self-check afterwards
gitvow uninstall --user [--agent NAME] [--purge-policy] [--purge-ledger] remove the user install; with no --agent, removes gitvow from every agent configured there
gitvow uninstall [repo] [--purge-notes] [--purge-snapshots]  remove a repository install; --purge-notes deletes local refs/notes/gitvow/*, --purge-snapshots deletes refs/gitvow/snapshots/*
gitvow check -- <command...>                              dry-run the policy against a Bash command; exit 0 allow or confirm at commit, 2 blocked
gitvow check --path <file>                                dry-run against an edited path
gitvow check --mcp <tool-name>                            dry-run against an MCP tool name
gitvow status [--json]                                    check the install is live: hooks reachable from the agent, policy and redaction rules load, git hooks in place, plus each agent's manual steps; exit 1 if anything is failing
gitvow show [commit]                                      print a commit's message, trailers and session note (default HEAD)
gitvow decisions [--json]                                 the card: open findings in this repository, numbered, with evidence and what the record proposes
gitvow rules [--json] [--write [--agent NAME | --file PATH]]  earned rules from the decision history, with candidates and lapsed rules; --write updates the managed section of an agent instruction file
gitvow revisit <commit> [accept|decline] [--finding N] [--scope S] [--reason R] [--by WHO]
                                                          list a commit's decisions, or answer one again with an empty commit carrying Gitvow-Revisits; the earlier trailer stays
gitvow decide <n|all> accept|decline [--scope S] [--reason R] [--by WHO]
                                                          record a person's answer to finding n (or every open finding); written as trailers on the next commit
gitvow redact <text>                                      apply the built-in layers plus your rules files to text and print the result
gitvow report --base <rev> [--head <rev>] [--json] [--require-notes] [--target BRANCH] [--decisions-summary]
                                                          per-commit report (trailers, notes, attribution, said vs did, decisions) for base..head; --target reopens decisions whose scope does not cover a production branch; --decisions-summary prints only the trailer block for a pull request description; exit 1 if --require-notes and a trailered commit has no note
gitvow ask <question> <subject> [--path <file>]         ask every configured provider and print the gate's decision
gitvow snapshots [--session ID] [--all]                   list working-tree snapshots taken after agent edits
gitvow snapshots prune [--older-than 14d] [--session ID]  delete snapshot refs
gitvow diff <session> <n> [--full]                        what the agent had changed at snapshot n, against the HEAD of that moment
gitvow restore <session> <n> [--to DIR]                   check a snapshot out into a detached scratch worktree
gitvow why <path>                                         which sessions shaped a file: commits, plans, attribution
gitvow trace <path>[:<start>-<end>]                       who wrote these lines: agent (session, step, plan) or person
gitvow recall <words...> [--limit N]                      sessions whose notes or ledger mention the words
gitvow handoff [--session ID]                             markdown summary for the next agent: plan, commits, files, uncommitted state, open confirmations
gitvow digest [--since 7d|YYYY-MM-DD] [--json]              period summary: agent vs human commits, sessions with plans and attribution, gate activity, most-changed files
gitvow push-notes [remote]                                push refs/notes/gitvow/* to the remote (default origin)
gitvow collect [--out DIR]                                gather ledger, logs, trailers and notes into one redacted directory (default ~/Desktop)
gitvow summarize <dir>                                    trial metrics from a collected directory
gitvow selftest                                           drive every hook in a throwaway repository and report pass/fail
gitvow --version
```

Exit codes: `0` success or allow; `2` blocked (hook and check); `1` error.

Environment: none required. `HOME` decides where the user policy and ledger live. Tests set `GIT_CONFIG_GLOBAL` to isolate git configuration.
