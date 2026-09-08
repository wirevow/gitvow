# Security

## Threat model
provkit writes trailers into commit messages, a structural note into a git ref, and a ledger into the user's home directory. It reads the agent transcript to build the note and the ledger. Transcripts contain tool output, which can contain secrets and personal data. provkit therefore never copies tool output anywhere; it records tool names, a shortened and redacted argument, and the agent's last stated plan after redaction.

## What leaves the machine
Nothing, by default. Notes are pushed only by an explicit `git push origin refs/notes/sessions`. There is no telemetry and no network call in provkit.

## Redaction is best effort
Layered patterns plus an entropy pass lower the probability that a secret reaches git. They cannot make it zero, and they cannot protect a repository from people who can already read it. Review the patterns before enabling on repositories that handle customer data; add custom rules for your own identifiers.

## Fail closed
A missing or invalid policy refuses every tool call. A classifier that fails or times out yields confirm, not allow.

## Supply chain
No runtime dependencies. CI runs ruff, bandit, pip-audit with `--strict`, CodeQL, and a build-and-check of the wheel on every change; Dependabot watches the development dependencies and the actions.

## Reporting
See `SECURITY.md` in the repository for the disclosure process. Please use synthetic secrets in reproductions.
