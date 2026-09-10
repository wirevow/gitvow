# Look at a repository before installing anything

```sh
pipx run gitvow scan            # or: pip install gitvow && gitvow scan
```

`gitvow scan` reads `git log` and nothing else. It writes nothing, sends nothing, needs no configuration, and works on a repository that has never heard of gitvow. Run it in any repository where agents have been working.

```
governance-intelligence · 39 commits in the last 90 days, merges excluded

  100%  agent-assisted           39 of 39 · Claude
    11  touched a gated file      Jenkinsfile, +3 more
     0  recorded who agreed

  Nothing is wrong with these commits. Nobody can tell you who agreed to them.

  Gated files in this window, by the rules in the default policy:
    Jenkinsfile  (5 commits)
    app/api/org/auth.py  (3 commits)
    app_saas/tenant/auth_api.py  (2 commits)
    app/api/platform/auth.py  (1 commit)
    app/mcp/auth.py  (1 commit)
```

## What each line means

**agent-assisted** counts commits whose message carries an agent's own signature: a co-author trailer from Claude Code, Cursor, Copilot, Codex, Gemini, Devin, Aider, Factory or Windsurf, the "Generated with Claude Code" line, or a `Gitvow-Session` trailer from gitvow itself. The agents named are the ones it found.

**touched a gated file** counts those commits that changed a file matching a `path_confirm` rule, so the definition comes from your own policy where you have one and from [the default](../reference/policy.md) otherwise. The output says which it used.

**recorded who agreed** counts those that carry a `Gitvow-Accepted` or `Gitvow-Declined` trailer. On a repository without gitvow this is zero by construction, which is the point of running it.

An open finding, if any, is a commit made by a person while an agent's finding was still unanswered.

## What it will not tell you

The count is a floor, not a total. An agent that leaves no trailer in the commit message is invisible to this scan, and plenty do not leave one. Once gitvow is installed it does not rely on trailers: the session is recorded from the hook, whether the agent signs its work or not.

It also says nothing about whether those commits were correct. A commit that changed an authorization file with nobody's judgment attached may have been completely right. The scan tells you that the judgment was not recorded, not that it was absent.

## Options

```sh
gitvow scan                      # the current repository, last 90 days
gitvow scan /path/to/repo        # somewhere else
gitvow scan --since 1y           # 30d, 6m, 1y or a date such as 2026-01-01
gitvow scan --json               # for a script
```

Nothing here needs gitvow installed in the repository. When you want the record to start, the next step is:

```sh
pipx install gitvow
gitvow install --user --check
```
