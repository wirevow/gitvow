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
Bump `version` in `pyproject.toml`, move the Unreleased section of `CHANGELOG.md` under the new version with today's date, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow builds, checks that the tag matches the version, publishes to PyPI through trusted publishing (no tokens stored anywhere), and creates the GitHub release with the changelog section as notes.

## Code of conduct
Be kind, be specific, assume good intent. Report conduct issues to the maintainers listed in `pyproject.toml`.
