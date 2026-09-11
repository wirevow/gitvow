# Security and the bypass surface

This page is the honest version. It says where gitvow's enforcement holds, where it does not, and what
you can run yourself to check each statement. Every command below was run against 0.15.2 in a throwaway
repository with a per-repository install, and the outcomes are what was observed rather than what was
expected.

## What gitvow does not claim

gitvow does not give you a tamper-proof audit trail, and no page here will say that it does.

A gitvow trailer lives inside the commit message. It is exactly as editable as the commit message it
lives in, by anyone who can rewrite the branch. A session note lives in a git ref that no forge protects.
There are three claims worth making, and they are the only ones made anywhere in these docs:

| Claim | What it means |
|---|---|
| **Measurable coverage** | How much of your agent-signed history carries a session record, computed from `git log` alone on a machine that trusts no client. `gitvow coverage`. |
| **Durability equal to the code** | The trailer is inside the commit; the note follows amend, rebase and squash through `notes.rewriteRef`. Losing the record means losing the commit. |
| **Verifiable non-forgery, once signing exists** | Not today. Today an acceptance is a line of text and anybody can type one. See [Forgery](#2-a-forged-acceptance-undetectable-today). |

## The three enforcement points

Enforcement is not one thing that is either on or off. It is three separate points that catch three
different failures, and they are not equally strong.

### Point 1 — on the machine (shipped)

The gate answers before the agent's tool call runs; git hooks then write the trailers and attach the
session note. This is where a person is actually asked, while the context is still in the room.

It is **bypassable by its owner, on purpose.** A developer owns their machine, and a gate that pretends
otherwise is theatre. This point catches accidents, not adversaries, and it guarantees nothing about
completeness.

### Point 2 — in CI (shipped)

A required check on the pull request: decisions present, session notes actually pushed, and coverage above
a floor you set. It reads the remote's own history, so nothing done on a laptop improves the number.

This is the point that matters, because it cannot be switched off from the machine that failed. Switching
it off is a change to a protected repository setting, which is itself visible. Shipped as
`gitvow coverage --fail-under` and the `require-notes` and `fail-under` inputs of the
[GitHub Action](guides/pull-requests.md).

### Point 3 — in the store, with signing (not built)

Signature verification on every acceptance, coverage computed across repositories rather than one at a
time, and a replica held under a retention policy the writer cannot shorten. This is what catches forgery.

!!! danger "None of this exists"
    Signing is not implemented anywhere in gitvow. Until it is, a forged acceptance is not detectable by
    `gitvow`, by the GitHub Action, or by reading the repository. This is not a gap being managed; it is
    the next thing to build.

## What was tested

Set-up for every row: a fresh repository, `gitvow install .`, a session state file with one accepted
finding, so that an ordinary commit produces the full trailer block.

```console
$ git commit -m "plain commit"
$ git log -1 --format=%B
plain commit

Gitvow-Session: sess-abc123
Gitvow-Step: 4
Gitvow-Accepted: edits an auth route by Tester scope=staging: reviewed
```

| Attempt | Observed | Verdict |
|---|---|---|
| `git commit --no-verify -m "…"` | The trailers are still written. `--no-verify` skips `pre-commit` and `commit-msg`; gitvow writes trailers from `prepare-commit-msg`, which it does not skip. The commit came out with `Gitvow-Session`, `Gitvow-Step` and `Gitvow-Accepted`. | **holds** — the most-guessed bypass does not work |
| `git -c core.hooksPath=/dev/null commit -m "…"` | No hook runs, so no trailer and no note. Reading the commit, it is indistinguishable from a hand-written one. | **bypassed** |
| the same commit, keeping the agent's own `Co-authored-by` line | `gitvow coverage` read the history and reported the hole: `1 commits carry an agent's signature / 0 of those are recorded by gitvow`. With `--fail-under 100` it exited 1. | **caught in CI** |
| the same commit with the `Co-authored-by` line deleted too | Not caught. Coverage counts only commits carrying an agent's own signature; a commit with no signature and no session is a human commit and nothing can prove otherwise. | **not caught** — coverage is a floor |
| `git push origin :refs/notes/gitvow/<session>` | The note is deleted from the remote. It had been pushed automatically by the `pre-push` hook, and one command removes it. The deletion is visible: the commit still carries `Gitvow-Session`, so `gitvow report --require-notes` exited 1 and named the commit. | **deletable, hole visible** |
| `git push --no-verify origin main` | `pre-push` is skipped, so the notes stay on the machine and the branch lands without them. Same hole as deletion, reached more quietly, visible the same way. | **deletable, hole visible** |
| `git commit -m "…" --trailer "Gitvow-Accepted=edits core/authz_rules.go by staff-engineer scope=production: reviewed with security"` | No bypass flag; hooks fully installed and running. `gitvow report` printed it as a real answer — `Accepted: … by staff-engineer, scope production` — `--decisions-summary` emitted it for the pull request description, and `--require-notes` exited 0. | **undetectable** |
| the same forgery plus a hand-typed `Gitvow-Session:` trailer | Coverage rose to `100%`: the forgery improves the metric. This clumsier version is caught for an unrelated reason — the session it claims has no note, so `--require-notes` exited 1. | **raises coverage** |
| the agent itself runs `git -c core.hooksPath=… commit` | The at-commit card never appears. gitvow recognises the agent's commit by matching the literal text `git commit`, which `git -c … commit` does not match, so open findings are neither shown nor recorded. The command is written to `.git/gitvow-hooks.log`, which nobody outside that machine reads. | **bypassed** — a policy rule closes it, [below](#closing-the-agents-own-bypass) |
| `git commit --amend` after a note was attached | The note followed the rewritten commit through the `notes.rewriteRef` setting the install adds; the trailers are inside the message and carried by construction. | **holds** |

### On branch protection

GitHub rulesets and branch protection rules
[target branches and tags](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets):
"You can create rulesets to control how people can interact with selected branches and tags in a
repository." `refs/notes/*` is neither a branch nor a tag, so nothing under it is protected on GitHub —
not by required checks, not by force-push protection, not by deletion protection. This was read from
GitHub's documentation, not tested against a live repository, and it is the reason the note check in CI
exists.

### Closing the agent's own bypass

One deny rule makes an agent's attempt to run git with gitvow's hooks disabled a refused tool call:

```json
{
  "bash_deny": [
    {
      "pattern": "\\bgit\\b[^|;&]*\\s(-c\\s+core\\.hooksPath|--no-verify)\\b",
      "reason": "git run with gitvow's hooks disabled"
    }
  ]
}
```

Checked with `gitvow check`, which is the dry-run for exactly this:

```console
$ gitvow check -- git -c core.hooksPath=/dev/null commit -m x
DENY: git run with gitvow's hooks disabled
$ gitvow check -- git commit --no-verify -m x
DENY: git run with gitvow's hooks disabled
$ gitvow check -- git push --no-verify
DENY: git run with gitvow's hooks disabled
$ gitvow check -- git commit -m x
ALLOW
$ gitvow check -- git -C sub commit -m x
ALLOW
```

That last line is the limit of the rule: `git -C dir commit` still gets past it, and past the card's own
`git commit` match. The rule constrains the agent. It does nothing about a person at a terminal, and it is
not meant to.

## Threats, worst first

Ranked by what each costs you, not by how clever it is.

### 1. Never installed at all, or the hooks disabled

By a wide margin the most likely way your record is wrong. Nobody attacked anything: a machine was never
configured, or an agent session was already open when gitvow went in. There is no drama and no signal,
which is exactly why it wins.

**Answered by** `gitvow coverage` computed from history, plus a required CI check. Both shipped.

### 2. A forged acceptance — undetectable today

Anyone who can write a commit message can attribute a decision to a named engineer who never made it,
with a production scope, and have gitvow's own report repeat it into a pull request as fact. It takes one
ordinary commit with a `--trailer` flag, with the gate installed and running and no bypass involved, as
reproduced in the table above. There is no check to add, no configuration to set and no reading of the
repository that finds it.

**Answered by** signing. Not built.

### 3. A note deleted after it was pushed

One command, and `refs/notes/*` sits outside everything GitHub lets you protect. It ranks below forgery
because it is loud: the trailer stays on the commit, so the missing note is a hole that any reader and
`gitvow report --require-notes` can see.

**Answered by** the CI note check (shipped) plus a store replica (not built).

### 4. A note deleted before it was ever pushed

Not a threat, and worth saying so rather than padding the list. A record that never left the machine is a
record of work that also never left the machine. If the code is not there either, nobody is missing
anything.

**Answered by** nothing needed.

### The asymmetry

A deleted record leaves a hole you can see. A forged one does not.

Every deletion above is survivable because the evidence of the deletion survives it. Forgery is different
in kind: it produces a record that reads correctly, satisfies gitvow's own checks and raises the coverage
number. It is the only failure here with no answer at all, and signing is the only thing that answers it.

One honest note about signing: a commit signature attests to who made a commit, not to whether the person
named in a trailer agreed to anything. Getting from one to the other is design work that is not finished,
so it is not described here as though it were.

## Configure the CI check

Point 1 is where the human moment happens. Point 2 is where completeness is enforced, because it is the
only one your developers cannot switch off.

```yaml
# .github/workflows/gitvow.yml
name: gitvow
on: pull_request
permissions:
  contents: read
  pull-requests: write
jobs:
  record:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {fetch-depth: 0}
      - uses: wirevow/gitvow@v0.15
        with:
          require-notes: "true"   # fail when a trailered commit's note was never pushed
          fail-under: "90"        # fail when coverage drops below this percentage
```

Then make the job a required check in the repository's branch protection or ruleset, or it is a comment
rather than a gate. Without the action:

```console
$ gitvow coverage --since 90d --fail-under 90
$ gitvow report --base "$BASE" --head "$HEAD" --require-notes
```

Two things worth getting right:

- **Set the floor where your history already is.** `gitvow coverage` tells you; a floor you have to lower
  next week teaches everyone to ignore it. `fetch-depth: 0` matters, because coverage reads history.
- **Coverage is install health, never anybody's performance.** `gitvow coverage --who` lists the commit
  authors whose machines still need configuring, and that is the only thing it is for.

## A defect that shipped

gitvow was installed into its own repository. Every agent except Cursor reads its hook configuration once,
at session start — so the session already open kept running with no gate, wrote commits with no session
record, and `gitvow status` reported the install as healthy. Installed, inert and silent: the worst of the
three states, and it produces a clean-looking record of ungated work with every number agreeing.

Nothing found this but using it. Fixed in 0.15.1 by testing for the consequence rather than repeating the
advice: an agent-signed commit made after the install that carries no session is now a failure with a
named remedy.

```console
$ gitvow status
  FAIL 1 agent commit since the install (2026-09-10 15:58) carries no session: f1bf662
         an agent session that was already open when you installed is not gated, because
         hooks are read at start-up. Restart it, then `gitvow coverage` to confirm.
         Cursor reloads by itself.
```

0.15.2 then fixed the fix: `git log --since` has one-second granularity, so a commit made in the same
second as the install was blamed on it. CI caught that on every runner while the machine it was written on
was too fast to ever see it.

## What leaves the machine

Not "nothing", which is what this page used to say. Two things go to your remote once you push:

- **Commit trailers**, because they are inside commits.
- **Session notes.** The `pre-push` hook pushes `refs/notes/gitvow/*` to the remote you are pushing to,
  automatically, whenever you push a branch. `gitvow push-notes [remote]` does it by hand. `git push
  --no-verify` skips it.

Everything else stays local: snapshots under `refs/gitvow/snapshots/` (never pushed, and excluded from the
`pre-push` hook), attribution blobs in `.git/objects`, the per-session JSON files under `~/.gitvow/ledger/`,
the hook log in `.git/gitvow-hooks.log`, and the agent's transcript, which gitvow never copies anywhere.
See [What stays out of git](concepts/storage.md).

There is no telemetry and no network call anywhere in gitvow. It has no runtime dependencies.

## Threat model for content

gitvow reads the agent transcript to build the note and the session record. Transcripts contain tool
output, which can contain secrets and personal data. gitvow therefore never copies tool output anywhere:
it records tool names, a shortened and redacted argument, and the agent's last stated plan after redaction.

### Redaction is best effort

Layered patterns plus an entropy pass lower the probability that a secret reaches git. They cannot make it
zero, and they cannot protect a repository from people who can already read it. Read the patterns before
enabling this on repositories that handle customer data, and add rules for your own identifiers. See
[Redaction and custom rules](guides/redaction.md).

### Fail closed

A missing or invalid policy refuses every tool call. A classifier that fails or times out yields confirm,
not allow. A broken external agent adapter blocks during `PreToolUse`. An invalid redaction rules file
stops notes, session records and log details from being written at all, rather than writing them
under-redacted.

### Snapshots

A snapshot is a commit object holding the working tree after an agent edit, minus ignored files and the
secrets exclusion list. It lives under `refs/gitvow/snapshots/`, which no gitvow command pushes and which
the `pre-push` hook excludes. Anyone with read access to the repository directory can read snapshots,
exactly as they can read the working tree. Extend `snapshots.exclude` for files that must never be
captured, or disable snapshots in the policy.

### Attribution blobs

To attribute lines, gitvow stores the version of a file the agent wrote as a loose git object in
`.git/objects`. It is unreachable from any ref, is never pushed, and `git gc` prunes it on the normal
schedule. It contains exactly what was already in the working tree.

## What the gate cannot see

It inspects tool calls the agent makes through its hooks. A Bash command is inspected as text; a process
it spawns is not seen separately. Path checks use the path, not the diff. Regular expressions are a floor;
[providers](concepts/providers.md) and the classifier are how you raise it.

## Supply chain

No runtime dependencies. CI runs ruff, bandit, pip-audit with `--strict`, CodeQL, and a build-and-check of
the wheel on every change; Dependabot watches the development dependencies and the actions. Releases go to
PyPI through trusted publishing, so no token is stored anywhere.

## Reporting

See `SECURITY.md` in the repository for the disclosure process. If you run one of the commands above and
get a different result, that is a bug and it is wanted. Please use synthetic secrets in reproductions.
