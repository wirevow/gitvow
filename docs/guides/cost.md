# What each session cost

Every transcript gitvow reads carries token counts. gitvow adds them up per session, prices them with a table you control, and writes the result everywhere a session appears: the session note on each commit, the ledger, the pull request report, the digest and the handoff.

## What is recorded

```json
"usage": {
  "input_tokens": 182340,
  "output_tokens": 9120,
  "cache_read_tokens": 141200,
  "cache_write_tokens": 20050,
  "reasoning_tokens": 0,
  "models": {"claude-fable-5-1": 41},
  "estimated_cost_usd": 1.94,
  "pricing": "gitvow defaults 2026-09"
}
```

| Field | Meaning |
|---|---|
| `input_tokens`, `output_tokens` | totals across the session so far |
| `cache_read_tokens`, `cache_write_tokens` | prompt-cache traffic, priced separately where the vendor does |
| `reasoning_tokens` | thinking tokens where the agent reports them (Codex, Gemini) |
| `models` | model names seen, with the number of responses each produced |
| `estimated_cost_usd` | tokens × the pricing table; an estimate, never a bill |
| `pricing` | which table produced the estimate |

Sources: Claude Code reports usage on every assistant message; Codex reports running totals in `token_count` events; Gemini CLI reports per-message `tokens`. Cursor, Copilot CLI and Factory transcripts are not read, so their sessions show no usage.

## Pricing
gitvow ships a default table of list prices per million tokens for the common models, dated in the note so a reader knows what the estimate assumed. Override or extend it in the policy file when your prices differ or a model is missing:

```json
"pricing": {
  "claude-fable-5-1": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
  "gpt-5": {"input": 1.25, "output": 10, "cache_read": 0.125},
  "my-internal-model": {"input": 0, "output": 0}
}
```

Keys match the model name as it appears in the transcript; a prefix match is accepted (`claude-fable` matches `claude-fable-5-1`). An unpriced model contributes tokens but no cost, and the note says which model was unpriced.

## Where it shows

```
$ gitvow digest --since 30d
Commits: 41 · by agents 29 (71%) · by people 12
Sessions: 9 · agent share 0.83 · lines changed by people after agents 212
Cost: $23.60 estimated · 4.1M input, 0.3M output tokens · 2 sessions unpriced

### Sessions
8f3d5c71  09-09  4 commits  share 1.00  $4.12  "Change the cache key ..."
```

`gitvow report` adds tokens and cost to each agent commit on a pull request; `gitvow handoff` states what the session has cost so far.

## Subagents
Claude Code runs delegated work as side conversations inside the same transcript; gitvow counts them and their tool calls under `subagents` in the note. Cursor announces subagents through hooks; the adapter records each one in the hook log. Their tokens are included in the session's totals when the transcript carries them.

## Calibration
On 2026-09-09 a real Claude Code session was measured against the cost Claude Code itself reported: gitvow's token counts matched to within the final turn (the note is written at commit time, before the closing message), and the estimate was within ten percent once the model's own price was in the table. Before that entry existed, a prefix match to an older generation overstated the cost by about two times. When a new model appears, add its price under `pricing` rather than trusting a prefix match, and `unpriced_models` in the note tells you when no price applied at all.

## Honesty
Estimates use list prices unless you override them, ignore discounts and free tiers, and depend on what the agent reports. Treat them as a trend and a comparison between sessions, not an invoice.
