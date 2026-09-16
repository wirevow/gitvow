# The export bundle

`gitvow export` assembles the record into a directory a security team can read before anything moves. It carries the record, never the session: no transcript text, no working tree, no snapshot, no command line, no prompt. Nothing in this release sends; the bundle is the shape every sink will receive.

```
gitvow export --dry-run --why          # what would leave, what never does, what was refused; writes nothing
gitvow export --since 6m --out ./bundle
gitvow export --consent decisions,claims
```

## Layout

```
bundle-<head7>/
  manifest.json        # schema, kind, gitvow version, source, frontier, consent, parts with sha256, digest
  attestation.json     # redaction rules digest, rows refused and why, what is never carried, signature (reserved)
  decisions.jsonl      # one row per decision trailer, with its note fields when the note is present
  claims.jsonl         # confirmed claims: id, text, paths, speaker, who confirmed with what standing
  observed.jsonl       # Gitvow-Observed rows: finding, commit, date; no answer, no person
  rule-verdicts.jsonl  # Gitvow-Rule-* with the frozen evidence run from the rules note
  sessions.jsonl       # consented only: counts, attribution, usage, edits-elsewhere counts; never the plan text or paths
  gate-events.jsonl    # consented only: counts per session per reason; never a command
  meter.json           # commits, agent-signed commits, agent-authored lines, window, floor: true
```

One file per consented data class. A class not consented has no file, and the manifest's `consent` map says so, so a reviewer learns what travels by listing the directory.

## Consent

The committed file `.gitvow/export.json` is the repository's statement of what it lets leave:

```json
{"consent": {"decisions": true, "claims": true, "observed": true, "rule_verdicts": true, "sessions": false, "gate_events": false, "meter": true}}
```

Those are also the defaults. `--consent a,b` overrides the file for one run. Consent widens only by a commit to that file, which is reviewed like any other change.

## Redaction is re-verified, never applied

Every string in every row runs through the repository's redaction rules at export. A row that would change is **refused** and listed in `attestation.json` with the part, the row number and a digest of the offending field. It is never rewritten: a rewritten row would mean the write-time edge failed and the failure was hidden. Identifiers the record itself mints (claim ids, commit shas, session ids, digests) are exempt, since they look like tokens to the redactor and carry nothing.

## Manifest

| Field | Meaning |
|---|---|
| `kind` | always `record`; a raw-session artefact, if a customer ever produces one with a different command, is a different kind and is refused by every hosted sink |
| `source.remote` | the normalised `origin` (host/owner/repo, no scheme, no `.git`), the join key across bundles from the same repository |
| `frontier` | `head`, the note refs and their tips, the `since` window, `as_of`; exactly what this bundle covers |
| `parts[]` | `path`, `class`, `sha256`, `rows`, `bytes` per file |
| `digest` | sha256 over the part digests, in class order; the idempotency key. Sending the same bundle twice changes nothing |

Parts are serialised canonically: rows sorted, keys sorted, no timestamps the exporter invented inside a part. The same repository at the same frontier yields the same digest.

## Rows

Every row carries `source` and `sha` so it is meaningful on its own. `decisions.jsonl` rows carry `finding`, `kind`, `path`, `answer`, `by`, `authority`, `scope`, `to`, `note`, `human_turns_after_card`, `proposed`, `session_id`, `reason` and `binding` (`note` when the session note was present, `trivial` otherwise). `class` is `null` in this version: not computed, never "no class". `sessions.jsonl` rows carry `last_stated_plan: null`; the plan text is a sub-class that is off and not exportable here. Paths of edits made in other checkouts are never exported, only their counts.

## What never leaves

Transcripts, working trees, snapshots, command lines, prompts, and person-reach claims (a person's own preferences, which live in their `~/.gitvow/claims/` and nowhere else). The attestation states this list so it is on the record, not in a footnote.

See also [Session note schema](note.md) and [Decisions](../concepts/decisions.md).
