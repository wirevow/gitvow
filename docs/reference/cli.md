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
gitvow scan [repo] [--since 90d|6m|1y|DATE] [--json]  read an existing repository's git history: how much an agent wrote, how much of it touched a gated file, and how much records who agreed; needs no install and writes nothing
gitvow coverage [repo] [--since 90d] [--who] [--fail-under N] [--json]
                                                          is the record complete? agent-signed commits carrying no session trailer, computed from history so it trusts no client; each session claim is corroborated against the note behind it, and claims with no note are counted and listed; exit 1 below --fail-under
gitvow status [--json]                                    check the install is live: hooks reachable from the agent, policy and redaction rules load, git hooks in place, plus each agent's manual steps; exit 1 if anything is failing
gitvow carry <base>..<head> <target> [--json]             carry the session notes of the commits in the range onto the commit that replaced them (a squash merge): one note per session, sources under carried_from, decisions merged; idempotent; then push refs/notes/gitvow/*
gitvow serve [--repo <path>] [--print-config]             the record server: this repository's record over MCP (stdio) for any agent; read-only, writes nothing, no network; --print-config shows how to register it with Claude Code, Cursor and Codex
gitvow status --line                                      one line for an agent's status bar, from this repository's session state and hook log: `[gitvow 0.26.0] 2 decided · 5 recorded · 1 open · 3 asked · 0 denied`; says "no session recorded here" rather than printing zeros
gitvow show [commit]                                      print a commit's message, trailers and session note (default HEAD); on a commit written by `gitvow rules accept|reject`, the rule-decision note with its evidence
gitvow decisions [--json]                                 the card: open findings in this repository, numbered, with evidence and what the record proposes
gitvow rules [--json] [--write [--agent NAME | --file PATH]]  rules in force, the proposals awaiting an authority, and what is neither yet (short of the threshold, rejected, lapsed); --write updates the managed section of an agent instruction file
gitvow rules accept <n|finding|--all> [--reason R] [--by WHO]
                                                          an authority accepts a proposed rule, making it a rule: empty commit carrying Gitvow-Rule-Accepted plus its evidence on refs/notes/gitvow/rules; --all adopts every proposal standing, which is the upgrade path for rules earned before 0.16; refused when the person is not named under decisions.authorities
gitvow rules reject <n|finding|--all> [--reason R] [--by WHO]
                                                          an authority rejects a proposed rule; it returns only after a full rule_threshold of answers dated later than the rejection
gitvow policy [--json]                                    proposed policy rules waiting for a person, highest consequence first, each with its evidence and its price in questions per engineer per week
gitvow policy import <file.json>                          queue proposals: the loop's JSON output, a list of candidates, or a policy fragment
gitvow policy accept <n|id> [--when commit|observe|immediate] [--reason R] [--by WHO]
                                                          write the rule into .gitvow/policy.json (created from the shipped default if absent) and commit that change alone, with Gitvow-Policy-Accepted and the evidence in the message
gitvow policy reject <n|id> [--reason R] [--by WHO]      an empty commit carrying Gitvow-Policy-Rejected; the loop stops proposing it
gitvow sync [--sink NAME] [--since 90d] [--dry-run]   the collector: export the record and deliver it to the configured sinks (git store, directory, or a store over http); queues in ~/.gitvow/outbox when a sink is unreachable, reports a refusal without queueing; idempotent by digest
gitvow sinks [--json]                                     the configured sinks, read only from .gitvow/export.local.json (never committed) and ~/.gitvow/sinks.json
gitvow pack [--json]                                      the organisation pack cached for this repository: rules it adds, who accepted them, when it lapses; NOT applied and why, when it is not
gitvow brief [--json]                                     what the record says about this repository: the store's cached brief with its age (source: cache), or the repository's own record (source: repo)
gitvow export [--since 90d] [--out DIR] [--consent a,b] [--dry-run] [--why]
                                                          the export bundle: decisions, confirmed claims, observed findings, rule verdicts and the meter, plus sessions and gate-event counts when consented, as one directory with a manifest and a redaction attestation; never a transcript, working tree, snapshot, command or prompt
gitvow claims [--json] [--write [--agent NAME | --file PATH]]  candidate claims waiting for a person, most confident first; --write renders the confirmed ones into the agent's instruction file under their own managed section
gitvow claims import <file.jsonl>                         queue candidates (one JSON object per line: claim_id, text, speaker, kind, paths, confidence, source); person-reach preferences go to ~/.gitvow/claims, never into the repository
gitvow claims confirm <n|id> [--paths a/,b/] [--edit TEXT] [--reason R] [--by WHO]
                                                          confirm a claim as true of this repository: empty commit carrying Gitvow-Claim-Confirmed plus a note with the verbatim text, its source and the edit if any
gitvow claims reject <n|id> [--reason R] [--by WHO]      reject a claim; it is not asked again, and a later rejection withdraws an earlier confirmation
gitvow claims show <id>                                   one claim's record
gitvow revisit <commit> [accept|decline|refer] [--finding N] [--scope S] [--to WHO] [--reason R] [--by WHO]
                                                          list a commit's decisions, or answer one again with an empty commit carrying Gitvow-Revisits; the earlier trailer stays
gitvow decide <n|all> accept|decline|refer [--scope S] [--to WHO] [--reason R] [--by WHO]
                                                          record a person's answer to finding n (or every open finding); written as trailers on the next commit. refer means "not my call": it closes the card, is tracked apart from open debt, and never counts towards a proposed rule
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
gitvow digest [--since 7d|YYYY-MM-DD] [--json]              period summary: agent vs human commits, sessions with plans and attribution, gate activity, most-changed files, decision debt and referrals listed apart, proposed rules awaiting an authority
gitvow push-notes [remote]                                push refs/notes/gitvow/* to the remote (default origin)
gitvow collect [--out DIR]                                gather ledger, logs, trailers and notes into one redacted directory (default ~/Desktop)
gitvow summarize <dir>                                    trial metrics from a collected directory
gitvow selftest                                           drive every hook in a throwaway repository and report pass/fail
gitvow --version
```

Exit codes: `0` success or allow; `2` blocked (hook and check); `1` error.

Environment: none required. `HOME` decides where the user policy and ledger live. Tests set `GIT_CONFIG_GLOBAL` to isolate git configuration.
