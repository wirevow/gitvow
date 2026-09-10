# Contributing

Thank you. gitvow is small on purpose: standard library only, one CLI, hooks that exit 0 or 2. Please keep it that way.

## Ground rules
- **No runtime dependencies.** Anything that needs a third-party package belongs in an optional extra or outside this project.
- **Every behaviour has a test.** Redaction patterns get positive and negative cases. Policy rules get a deny, a confirm and an allow case. Anything touching git runs against a real temporary repository.
- **Docs first.** Change `docs/` in the same pull request as the code, and change the docs before the code when the behaviour is user-visible.
- **Fail closed.** If a hook cannot decide, it blocks.
- **Never widen what enters git.** Notes hold structure and redacted text, never tool output.
- **Our vocabulary**: session, step, session note, ledger, gate, policy, self-check. Do not import terminology from other products.

## Development
```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
bandit -q -r src
pip freeze --exclude-editable | grep -v '^gitvow' > /tmp/req.txt && pip-audit --strict -r /tmp/req.txt
mkdocs serve   # docs at http://127.0.0.1:8000
```

## Pull requests
- One change per PR. Describe the behaviour change and the user-visible effect.
- Add a line to `CHANGELOG.md` under Unreleased.
- CI must be green: lint, tests on 3.9–3.12 across Linux and macOS, bandit, pip-audit, CodeQL.

## Releasing
Bump `version` in `pyproject.toml`, move the Unreleased section of `CHANGELOG.md` under the new version with today's date, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow builds, checks that the tag matches the version, publishes to PyPI through trusted publishing (no tokens stored anywhere), creates the GitHub release with the changelog section as notes, and moves the `vMAJOR.MINOR` tag that workflows reference as `wirevow/gitvow@vX.Y`.

## Code of conduct
Be kind, be specific, assume good intent. Report conduct issues to the maintainers listed in `pyproject.toml`.

## This repository gates itself

gitvow is installed here, for every agent it supports, so the tool is exercised on its own history. After cloning:

```sh
git config core.hooksPath .gitvow/git-hooks
```

That is all. From then on, an agent session in this repository passes the same gate everyone else gets: destructive commands are refused, an edit to a gate-bearing path or a CI definition waits for the commit, and the commit is refused once with a card until somebody answers it. The policy is `.gitvow/policy.json` and it is reviewed in pull requests like any other change.

Two honest caveats, both of which we would rather you knew than discovered:

- An agent already running when you clone will not be gated until it starts a new session, because agents read their hook configuration at start-up. `gitvow status` says whether the gate is live for you.
- Codex CLI needs its hooks trusted once with `/hooks`, and needs this repository's `.git` in `sandbox_workspace_write.writable_roots`, or it cannot commit at all. `gitvow install` prints both.

`gitvow coverage` on this repository will read low for a while. The gate was installed on 10 September 2026 and everything before that is uncovered by construction, which is exactly what coverage is supposed to show.
