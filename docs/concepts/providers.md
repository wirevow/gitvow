# Providers: policy from facts

A regular expression can say "this path looks like an authorization file". It cannot say "this route has four callers in two other services" or "this endpoint would be reachable without authentication". Those are facts about the codebase, and they live outside the repository the agent is editing.

A **provider** is a program that answers such questions for the gate. gitvow defines the questions and the answer shape; the provider owns the knowledge. gitvow ships none of that knowledge, only the contract and a small example.

## Questions the gate asks

| Question | When | Subject | A `yes` means |
|---|---|---|---|
| `gate_bearing` | before any Edit, Write, MultiEdit or NotebookEdit | the file path | this file decides who can do what; require a human |
| `route_gate` | before an Edit whose new text introduces a route literal such as `"/v1/orders"` | the route | the route would be exposed without the expected authorization; require a human |
| `route_callers` | before an Edit whose old text contained a route literal that the new text drops | the route | something else calls this route; require a human |

Route literals are quoted strings beginning with `/`, without spaces, that appear in the edit's text. The gate sees the agent's intent from the tool call before the file changes.

## What the agent sees

```
CONFIRMATION REQUIRED (provider topology: route /v1/orders/export is not covered by an authorization
filter; whitelist is auth/AuthorizeWhitelistedPaths.java; 3 existing callers of this service's
routes come from client-orch). Ask the user explicitly before doing this.
```

The evidence is the provider's, verbatim, so the agent can put a precise question to the person rather than a vague one.

## Failure behaviour
A provider that is missing, times out, crashes or returns something unparseable yields **confirm**, never allow, and the log records which provider failed. An `unknown` answer is treated as no evidence and does not block on its own.

## Where providers come from
Anyone can write one: a script over a facts database, an HTTP client to an internal service, a query against a code graph. The reference example in the repository reads a static JSON facts file and exists to show the contract and to drive the tests. [gitvow-provider-facts](https://wirevow.dev/gitvow-provider-facts/) is that production shape: a provider over a derived fact store of routes, gates and calls, in its own project.
