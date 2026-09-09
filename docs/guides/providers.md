# Wire a provider

## Start from the example
The repository ships `examples/providers/static_facts.py`, a complete provider over a JSON facts file:

```json
{
  "gate_bearing": ["auth/AuthorizeWhitelistedPaths.java", "deploy/values/production-*.yaml"],
  "whitelist_file": "auth/AuthorizeWhitelistedPaths.java",
  "authorized_routes": ["/v1/orders", "/v1/orders/*"],
  "callers": {"/v1/orders": ["client-orch (OrdersClient.java:88)", "billing-worker (sync.py:41)"]}
}
```

Wire it into the policy:

```json
"providers": [{"name": "facts", "command": "python3 examples/providers/static_facts.py --facts .gitvow/facts.json"}]
```

Then test:

```sh
gitvow ask gate_bearing auth/AuthorizeWhitelistedPaths.java
# facts: yes — listed as gate-bearing
# decision: CONFIRM
gitvow ask route_gate /v1/orders/export
# facts: yes — not covered by authorized_routes; whitelist is auth/AuthorizeWhitelistedPaths.java
```

## Write your own
Any language. Read one JSON object from stdin, write one to stdout, exit 0. Answer `unknown` for questions you cannot decide; never guess `no` to be quiet, because `no` is what lets an edit through without a person. Keep evidence strings short and free of secrets: they are shown to the agent and written to the hook log after redaction.

A production provider typically sits on a derived model of the organisation's services: which files carry authorization, which routes exist, who calls them, which are exposed. Building that model is a separate problem; the provider is the thin adapter between it and the gate.

## What to expect in practice
- `gate_bearing` fires rarely and is almost always right; keep its list tight.
- `route_gate` is the high-value question: an agent adding an endpoint that the authorization layer does not cover is exactly the change a reviewer wants to see before it exists.
- `route_callers` prevents silent removals; if it fires on routine refactors, have the provider answer `unknown` for routes with no external callers.
- Watch the hook log for `provider_failed` events: a provider that is often unavailable turns every edit into a confirmation, and people will remove it.
