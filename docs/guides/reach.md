# Who owns this path?

"Who decides about this?" had no answer inside the tool. Authority was a list in the policy file that went stale, a referral named whoever the person typed, and a finding on a path nobody owns looked exactly like one on a path someone does. Since 0.32, every finding that names a path carries its owner from the moment it is raised, read from the ownership file the repository already keeps.

```sh
gitvow reach core/authz_rules.go     # who owns this path, and from which rule
gitvow reach                         # decided paths with an owner, unowned paths carrying decisions
gitvow reach --json
```

## Where owners come from

1. **The ownership file at HEAD.** `CODEOWNERS`, `.github/CODEOWNERS`, `docs/CODEOWNERS` or `.gitlab/CODEOWNERS`, the first found, read from the commit, not the working tree, and recorded with the commit that last changed it (`.github/CODEOWNERS@4356ffa1b2c3`). Matching follows the CODEOWNERS rules: last matching line wins; `*` does not cross `/`; `**` does; a pattern with a slash anywhere but the end is anchored to the root, one without a slash matches a file or directory of that name anywhere; a trailing `/` names a directory and everything under it; a line with a pattern and no owner makes the path explicitly unowned. GitLab section headers are skipped.
2. **`decisions.authorities`** in the policy, when the file has no rule for the path, or there is no file.
3. Otherwise the path is **unowned surface**.

Owners are kept as written: `@team`, `@person`, an email. No forge is asked and no team is expanded, so an owner is a name the record can route a question to, not a resolved list of people.

## Where the owner goes

- **On the finding**, the moment the gate raises it, and so **on the card**:

```
1. edit core/authz_rules.go
   why: authorization file
   owner: @acme/platform (.github/CODEOWNERS@4356ffa1b2c3, /core/)
   not their call? gitvow decide 1 refer --to @acme/platform
   record: no earlier decision.
2. edit infra/iam/roles.tf
   owner: none (unowned surface; .github/CODEOWNERS@4356ffa1b2c3 has no rule for it)
```

- **On a referral.** `gitvow decide 1 refer` with nobody named goes to the path's owner; a name the person gives always wins. The trailer reads `Gitvow-Referred: … to=@acme/platform`.
- **In the note**, on each decision: `owner` (owners, source, pattern) and `unowned` (schema 9).
- **In `gitvow report`** and the pull request comment: the owner beside each decision, and `unowned path` in bold where there is none.
- **In `gitvow digest`**, one line:

```
Reach: 7 of 9 decided paths have an owner · 2 unowned paths carry decisions · owners from .github/CODEOWNERS@4356ffa1b2c3
```

- **The record server** answers `record_reach`, with a path or for the whole repository.

Command findings (a push, a cluster mutation) name no path and carry no owner; they are not counted in the line above.

## The two numbers

The share of decided paths with a resolved owner, and the count of paths carrying decisions that nobody owns. The digest resolves history against the ownership file at HEAD, so it answers "under today's ownership, who owned what was decided"; the note on each commit keeps the owner as it was at the time.

Unowned paths carrying decisions are the list to read first. They are where decisions get made by whoever happens to be at the keyboard, and where a referral has nowhere to go.

## What it is not

- Not enforcement. An owner on the card is context and a default for a referral; it never blocks, asks or allows anything.
- Not a check that the decider was the owner. Deciders are recorded by git identity and owners as CODEOWNERS handles, and gitvow does not ask the forge which people a handle contains.
- Not the organisation's reach. A pack's reach across repositories is the store's business; this reads one repository's own file.
