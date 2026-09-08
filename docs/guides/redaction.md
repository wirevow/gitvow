# Redaction and custom rules

Every string that enters a note, the ledger or the log passes through redaction first. Layers, in order:

1. **Custom rules** you supply as (pattern, replacement) pairs.
2. **Known formats**: AWS access keys, JWTs, GitHub tokens, `sk-` API keys, Slack and Google API keys.
3. **Credential assignments**: `password=`, `token=`, `secret=`, `api_key=`, `authorization:` and `bearer` values; the key is kept, the value replaced.
4. **Connection strings** for MySQL, Postgres, Redis, MongoDB, AMQP.
5. **Private key blocks.**
6. **Personal data shapes**: email addresses (replaced by a short stable hash so repeats are recognisable), card-like and SSN-like numbers, IP addresses.
7. **High-entropy tokens**: alphanumeric runs of 20 or more characters whose Shannon entropy exceeds 4.5, unless they look like a URL or a path. This catches secret formats nobody wrote a pattern for.

## What is never redacted because it is never read
Tool output. provkit records the tool name and a shortened argument, never the result. A `cat .env` is logged as `cat .env`, and the contents of `.env` go nowhere.

## Custom rules
Add patterns for identifiers specific to your systems, customer ids, internal hostnames, ticket formats that carry names:

```python
from provkit.redact import redact

redact(text, custom=[(r"CUST-\d{6}", "[customer]"), (r"[a-z0-9-]+\.internal\.example\.com", "[internal-host]")])
```

A configuration file for custom rules is planned; see the roadmap.

## Testing your redaction
Every pattern has positive and negative tests in `tests/test_redact.py`. Add yours there. The self-check also asserts that a planted token and email do not survive into a note.

## Honest limits
Redaction lowers the probability that a secret reaches git. It cannot make it zero. It does nothing about a secret that is already in the repository, and nothing about people who can already read the repository. Decide deliberately whether `refs/notes/sessions` is pushed and where.
