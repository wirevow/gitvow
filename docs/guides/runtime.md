# Runtime facts at the gate

The record knows what people decided about a path. From 0.39 it can also know what the running system does on
that path: how often a route is called, how slow it is, how often it fails. Those facts come from a runtime facts
provider your organisation runs against its own metrics store; the store holds them as **observations** and the
brief carries them to your laptop. gitvow never calls a metrics API. It reads the cached brief and nothing else.

## What you see

```sh
$ gitvow brief
What the organisation's record says about github.com/acme/orchestrator (store cache, 3 h old)
- edit:policy.gate-bearing: declined x4 in 3 repo(s); observed 11
- runtime facts on record: 3
  - GET /v1/orders/{id} in api/orders.py: 1700 requests in 7d, p99 79 ms, 5xx 0.0%, no SLO defined (26 h old) [obs_3f9c…]
  - POST /v1/orders in api/orders.py: 0 requests in 7d, no SLO defined (26 h old) [obs_a01b…]
  - GET /v1/reports in api/reports.py: 200 requests in 7d, p99 790 ms, 5xx 3.0%, no SLO defined (26 h old) [obs_7c22…]
```

Every fact names its observation. The same ref appears on the finding the gate raises from it, on the decision that
answers the finding, in the commit's note and in the export bundle. A person reading the record months later can
follow the ref back to the numbers the question rested on.

## Two findings the facts raise

When the agent edits a file that carries a route the store has facts for:

- **Silent route.** The route was measured at zero requests over the window. By default an observe-tier finding:
  nobody is asked, the commit records that dead surface was touched, and the record shows how often that happens.
- **Erroring route.** The route's 5xx share is at or above `runtime.error_rate_confirm`, default one percent. By
  default a commit-tier finding: the card asks a person before the change lands.

```
2. edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)
   why: route erroring: 5xx 3.0% over 7d
   evidence: 200 requests in 7d, p99 790 ms, 5xx 3.0%, no SLO defined, as of 2026-10-12T06:00:00Z (orchestrator, production-in)
   evidence: observation obs_7c22…
   evidence: reopens on: deploy of orchestrator in production-in; telemetry coverage change
```

A fact that is `no_telemetry` or `not_joined` raises nothing: the brief shows the gap, the gate does not guess.
A fact older than `runtime.max_age_hours` (a week) is shown with its age and not acted on.

## Settings

In `.gitvow/policy.json` or `~/.gitvow/policy.json`:

```json
{"runtime": {"silent_route": "observe", "erroring_route": "commit", "error_rate_confirm": 0.01, "max_age_hours": 168}}
```

Tiers are `observe`, `commit`, `immediate` or `off`. The default policy is a starting point: an estate whose
routes legitimately sleep for a quarter (billing runs, annual jobs) will raise `max_age_hours` and keep
`silent_route` at `observe`; one with a tight error budget will lower the threshold.

## Where the facts come from

The runtime facts provider (`wirevow runtime observe`, on the paid side) reads your routes from the code and your
metrics store over a window, joins them through a small identity table an owner confirms once as a claim, and
writes one observation per route with its coverage stated: measured, no telemetry for the service, or not joined.
It posts them to the store; `gitvow sync` brings the brief back. Nothing here needs a graph, a model or a change
to your pipeline.
