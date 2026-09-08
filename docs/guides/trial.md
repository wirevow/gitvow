# Run a trial and collect results

A two-week trial with five people answers three questions: how often the gate fires and on what, whether confirmations feel reasonable, and whether anyone reads a note.

## Setup, per person
```sh
pip install gitvow
gitvow install --user
gitvow selftest
```

## During the trial
Nothing. Work normally. The hook log fills in each repository's `.git`, the ledger fills in the home directory, notes attach to commits.

## Collect
```sh
gitvow collect            # → ~/Desktop/gitvow-<timestamp>/ with SUMMARY.txt
```
It reads the ledger to learn which repositories sessions touched, then gathers each repository's hook log, the commits carrying trailers, the notes and the remote URL. Everything in it was redacted at write time. Review the directory before sending it anywhere.

## Read the numbers
```
sessions: 3   repos touched: 2
tool calls: 118 | by tool: {'Bash': 61, 'Read': 30, 'Edit': 19}
commits during sessions: 5
hook decisions: {'allowed': 112, 'blocked': 2, 'confirm_required': 4, 'note_added': 5}
commits with trailers: 5 | notes attached: 5
gate fired on:
  confirm_required    4  pushing to a remote
  blocked             2  force push
```

`gitvow summarize <dir>` recomputes this for any collected directory, so one person can merge several.

## What to decide afterwards
- Rules that fired on routine work: relax or delete.
- Rules that never fired: keep, they are free.
- Notes nobody opened: the trailer alone may be enough for your team, or the note needs to reach the pull request.
- Whether the redacted ledger should move to a shared place. See [What stays out of git](../concepts/storage.md).
