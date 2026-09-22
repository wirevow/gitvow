"""0.28.2: the git rules are program rules, so `git -C <dir> push` and `git -c k=v push` are seen.

Found by dogfooding: every push made through `git -C <repo> push` on 2026-09-22 walked past the `pattern` rule
`\\bgit\\s+push\\b`, which needed the two words adjacent. Same bug class the kubectl rules had until 0.17.1.
"""

import json
from pathlib import Path

import pytest

from gitvow.policy import DEFAULT_POLICY_PATH, evaluate
from tests.conftest import git

C = "/Users/someone/work/repo"


@pytest.fixture
def pol():
    return json.loads(Path(DEFAULT_POLICY_PATH).read_text())


@pytest.mark.parametrize(
    "cmd",
    [
        "git push --force origin main",
        "git push -f",
        f"git -C {C} push -q -f origin v0.26",
        f"git -C {C} push --force-with-lease origin main",
        "git -c user.name=x push +refs/heads/main",
        f"git -C {C} reset --hard HEAD~1",
        f"git -C {C} clean -fd",
        "git branch -D feature",
    ],
)
def test_destructive_git_is_denied_whatever_sits_between_git_and_the_verb(pol, cmd):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == "deny", cmd


def test_a_push_to_a_protected_branch_asks_and_other_pushes_are_observed(repo, home, pol):
    git(repo, "remote", "add", "origin", "git@example.com:acme/ledger.git")
    d = evaluate(pol, "Bash", {"command": f"git -C {repo} push -q origin main"}, str(repo))
    assert d.outcome == "confirm" and d.when == "immediate" and d.reason == "pushing to a protected branch"
    assert d.findings[0]["finding"].endswith("@ remote=git@example.com:acme/ledger.git branch=main")
    d = evaluate(pol, "Bash", {"command": "git push origin feature/x"}, str(repo))
    assert d.outcome == "confirm" and d.when == "observe" and d.reason == "pushing to a remote"
    d = evaluate(pol, "Bash", {"command": "git push origin HEAD:refs/heads/release-2"}, str(repo))
    assert d.when == "immediate"
    # a push whose destination cannot be read (no such remote) is asked about
    d = evaluate(pol, "Bash", {"command": "git push nowhere feature/x"}, str(repo))
    assert d.when == "immediate"


def test_mentions_of_push_inside_arguments_are_not_pushes(pol):
    assert evaluate(pol, "Bash", {"command": 'git commit -m "docs: how to git push safely"'}).outcome == "allow"
    assert evaluate(pol, "Bash", {"command": "echo 'git push --force is denied' >> notes.md"}).outcome == "allow"
