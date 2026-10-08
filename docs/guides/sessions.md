# Hand the session to your own store

The record bundle that `gitvow sync` sends carries conclusions: decisions, confirmed claims, observed findings, rule verdicts, counts. It has no file for the conversation, and that does not change.

`gitvow sessions export` is the other artefact. For one repository it gathers each session's transcript as the agent wrote it, the ledger entry, the session notes on this repository's commits, and the working-tree snapshots as patches, byte for byte, into a bundle of kind `sessions-raw`. Nothing in it is redacted. It is for a store **your organisation runs**, which holds it complete under your own retention and redacts it when someone reads it.

## Who can take it

- A store your organisation runs, when its operator has enabled `sessions-raw`. Otherwise it answers with a refusal and the reason.
- Nothing anyone else hosts. A hosted store refuses this kind under every configuration; there is no flag.

## Look first

```sh
$ gitvow sessions export --dry-run --why
sessions of github.com/acme/payments-api at 3f9c2a1b7d0e, since 2026-07-10
digest sha256:8e430e125da0…  3 session(s), 9 part(s), 412880 bytes

COMPLETE AND UNREDACTED. This is the conversation, not the record.
  accepted only by a store the customer runs whose operator has enabled sessions-raw; …

  8f3d5c71-574  2026-10-08T09:12  transcript included (401223 bytes), notes on 4 commit(s), 2 snapshot(s)
  0b1a9c22-e1f  2026-10-06T14:02  transcript missing, notes on 1 commit(s), 0 snapshot(s)
  …

missing on this machine: 1 transcript(s); the attestation lists them and says the bundle is incomplete
redaction: none at export: complete and unredacted; redaction is the receiving store's step, on read, …
never carried by: gitvow sync, a committed file, a hook
```

Nothing is written by `--dry-run`. A transcript that is not on this machine is named as missing, never guessed.

## Send it

```sh
gitvow sessions export --to team          # a configured http sink; the store decides
gitvow sessions export --out ./sessions   # write the bundle and move nothing
gitvow sessions export --since 30d --session 8f3d --no-snapshots --to team
```

`--to` names a sink from `gitvow sinks`. An http sink speaks the store protocol, the same verbs `gitvow sync` uses; a dir sink receives a copy under `<source>/sessions/<digest>/`. A git sink refuses: every clone would replicate the sessions and nothing would scrub them. An unreachable store is reported, not queued; there is no outbox for sessions.

## What is in the bundle

| Part | Content |
|---|---|
| `<session>.ledger.json` | the ledger entry: when, which repository, the intent, the plan, usage; for a session still open, the state so far |
| `<session>.transcript.jsonl` | the agent's transcript file, verbatim, whatever agent wrote it |
| `<session>.notes.json` | the session notes on this repository's commits (the same notes `gitvow show` reads) |
| `<session>.snapshot-NNN.patch` | each working-tree snapshot as a patch against the commit it was taken over |

`manifest.json` lists every part with its hash and size and the sessions it covers; `attestation.json` states that the bundle is unredacted, whether it is complete, who exported it and what never produces it.

## What this is not

- Not `gitvow sync`. The collector refuses to move a sessions bundle, whatever the sink.
- Not switched on by a committed file. `.gitvow/export.json` governs the record bundle and cannot name this.
- Not run by a hook. A person types it.

The people whose sessions these are should be told before the first export, by the organisation that runs the store. The attestation records who ran the command; it cannot record that the notice was given.
