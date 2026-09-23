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

## The real-harness check

The unit suite drives hook functions with synthetic payloads in temporary repositories. It cannot see what a real
agent does: dispatch tool calls in parallel, run a commit minutes after the gate saw it, reach into another
checkout, merge two settings files and run every hook twice. Seven of the first eight defects found in real use
lived there. `scripts/e2e_real.py` drives Claude Code itself:

```console
$ .venv/bin/python scripts/e2e_real.py            # a minute or two; a few cents on your own Claude account
real-harness check: OK in 71.3s, 9 turns, $0.32
```

It builds two sandbox repositories with bare remotes, installs this checkout's gitvow in both, writes the hooks
into an isolated `CLAUDE_CONFIG_DIR`, and asks the agent to edit and commit in both (one through `git -C`) and push
to a protected branch. Then it reads the record and asserts: trailers on both commits, notes, the production-values
finding recorded in the repository it belongs to, the card shown once, the push asked about and not made. `--keep`
leaves the sandbox for inspection; `--json` prints the summary as data.

It needs `claude` on PATH and a credential: `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), or on macOS the
keychain item Claude Code itself writes, which is read and never printed. Run it before a release and after any
change to `hooks/`, `install.py` or `policy.py`; a nightly run on a machine that is logged in is the intended home.

## Pull requests
- One change per PR. Describe the behaviour change and the user-visible effect.
- Add a line to `CHANGELOG.md` under Unreleased.
- CI must be green: lint, tests on 3.9–3.12 across Linux and macOS, bandit, pip-audit, CodeQL.

## Releasing
Bump `version` in `pyproject.toml`, move the Unreleased section of `CHANGELOG.md` under the new version with today's date, commit, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow builds, checks that the tag matches the version, publishes to PyPI through trusted publishing (no tokens stored anywhere), creates the GitHub release with the changelog section as notes, and moves the `vMAJOR.MINOR` tag that workflows reference as `wirevow/gitvow@vX.Y`.

## Code of conduct
Be kind, be specific, assume good intent. Report conduct issues to the maintainers listed in `pyproject.toml`.
