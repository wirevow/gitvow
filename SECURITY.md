# Security policy

## Threat model, in one paragraph
provkit writes three things: trailers into commit messages, a structural note into a git ref, and a ledger into the user's home directory. It reads the agent transcript to build the note and the ledger. The transcript contains tool output, which can contain secrets and personal data. provkit therefore never copies tool output anywhere; it records tool names, a shortened and redacted argument, and the agent's last stated plan after redaction. Redaction is layered (known formats, credential assignments, connection strings, private keys, personal-data shapes, high-entropy tokens) and is best effort: it lowers the probability that a secret reaches git, it cannot make it zero. Anyone who can read a repository can read every session note in it.

## What leaves the machine
Nothing, by default. Notes live in `refs/notes/sessions` and are pushed only when someone runs `git push origin refs/notes/sessions`. The ledger stays in `~/.provkit/ledger`. There is no telemetry and no network call anywhere in provkit.

## Reporting a vulnerability
Email nikhil@wirevow.com or open a private security advisory on GitHub. Please include the provkit version, the redaction pattern or code path involved, and a minimal reproduction with **synthetic** secrets. Expect an acknowledgement within 3 working days and a fix or mitigation within 30 days for confirmed issues. Do not open public issues for suspected leaks.

## Supported versions
The latest minor release receives fixes.

## Hardening checklist for adopters
- Review `default_policy.json` and the redaction patterns before enabling on repositories that handle customer data.
- Prefer the per-user install for trials; nothing is committed.
- Decide deliberately whether `refs/notes/sessions` is pushed, and to which remote.
- Add custom redaction rules for identifiers specific to your systems.
- Run `provkit selftest` after upgrades.
