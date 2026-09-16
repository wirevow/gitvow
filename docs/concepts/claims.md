# Claims, what the owners said

A decision is a person's answer to a question the gate asked. A claim is the other thing people know and never get asked about: what they say about their own system while they work. "We don't do any GET calls here, only List, CopyObject and DeleteObject." "The currency table is partitioned by month because the vendor files arrive monthly." "Only select queries, no insert or update without my knowledge." None of that is in the code, none of it is in a wiki, and all of it leaves with the person.

gitvow 0.19 gives a claim a place in the record, on one condition: **the person who said it, or someone with standing over the paths it is about, confirms it in their own words.** Until then it is a candidate, and a candidate is not context.

## Where candidates come from

Not from this package. A batch job outside gitvow reads sessions or documents and produces a JSONL file, one candidate per line:

```json
{"claim_id": "clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6D", "text": "we don't do any GET calls here, only List, CopyObject and DeleteObject",
 "speaker": "srikanth", "kind": "system", "travels_with": "code", "paths": ["ingest/s3/"], "confidence": 0.9,
 "why": "a standing constraint on the service", "source": {"kind": "session", "file": "s1.jsonl", "message": 2}, "when": "2026-08-14T05:12:50"}
```

The text must be verbatim. A claim reworded by a model is a record of what the model thought the person meant, ratified by someone moving fast; gitvow records what the person said and lets them edit it themselves.

## Two reaches

- **Repo-reach** (`travels_with: code`): true of the code regardless of who works on it. Queued in `.git/gitvow-claims.json`, confirmed into this repository's history.
- **Person-reach** (`kind: preference`): true of the speaker regardless of repository ("go to main and pull always when investigating"). Kept in the speaker's own `~/.gitvow/claims/preferences.jsonl`, read into their own sessions only, never committed, never exported.

## The queue and the verdict

```
gitvow claims import candidates.jsonl
gitvow claims                       # the queue, most confident first
gitvow claims confirm 1 --paths ingest/s3/
gitvow claims confirm 2 --edit "the currency table is partitioned by month; vendor files arrive monthly"
gitvow claims reject 3 --reason "not true since the June migration"
```

A verdict is recorded the way a rule verdict is: an empty commit carrying a new trailer name, plus a note.

```
Gitvow-Claim-Confirmed: clm_01K9TQ8ZP7X3F5M2WVJ4CNRB6D by srikanth paths=ingest/s3/: we don't do any GET calls here, only List, CopyObject and DeleteObject
```

The note on `refs/notes/gitvow/claims` holds the text as recorded, the original verbatim text and whether it was edited, the paths, the speaker, the source pointer, and the confirmer's standing: `speaker` when they said it themselves, otherwise the same authority a decision would carry. A later rejection of the same id withdraws it. Staged work is refused, so the commit carries nothing but the verdict.

## What a confirmed claim does

It informs. `gitvow claims --write` renders the confirmed claims into the agent's instruction file under their own managed section, each attributed and dated, with the sentence that they permit nothing. Session start hands the agent the repository's confirmed claims and, for the person at the keyboard only, their own preferences. The gate still asks; a claim never answers a finding.

## Why it is built this way

The knowledge that matters most is the kind everyone already observes, so nobody ever writes it down and no gate ever fires on it. Passive capture finds it in what people say. Ratification is what turns "someone said this once" into "the owner confirmed this is how the system works", and the difference between those two sentences is the whole reason to keep the record in git rather than in a wiki. See [Decisions](decisions.md) for the primitive this reuses.
