"""0.26.1: `{program, verbs}` rules match the program in command position, not anywhere in the line."""

import json
from pathlib import Path

import pytest

from gitvow.policy import DEFAULT_POLICY_PATH, evaluate, match_program_rule

K = "kubectl"
T = "terraform"


@pytest.fixture
def pol():
    return json.loads(Path(DEFAULT_POLICY_PATH).read_text())


@pytest.mark.parametrize(
    "cmd",
    [
        f'gh issue create --title "sizing" --body "how to size: {K} exec into the pod, then {K} get --raw /metrics"',
        f'git commit -m "docs: mention {K} delete for the runbook"',
        f"echo '{T} apply is described in README' > notes.txt",
        f"grep -rn '{K} exec' docs/",
        f"cat <<'EOF' > runbook.md\n{K} delete pod x\n{T} destroy\nEOF",
        f"python3 -c \"print('{K} scale')\"",
        f"kubectl get pods  # not: {K} delete",
    ],
)
def test_mentions_inside_arguments_are_not_commands(pol, cmd):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == "allow", cmd


@pytest.mark.parametrize(
    "cmd,outcome",
    [
        (f"{K} delete pod x", "deny"),
        (f"{K} --context prod -n ns delete pod x", "deny"),
        (f"sudo {K} delete pod x", "deny"),
        (f"KUBECONFIG=/tmp/k {K} delete pod x", "deny"),
        (f"timeout 30 {K} delete pod x", "deny"),
        (f"bash -c '{K} delete pod x'", "deny"),
        (f'sh -c "cd /x && {K} --context prod delete pod y"', "deny"),
        (f"echo pod x | xargs {K} delete pod", "deny"),
        (f"ls; {K} delete pod x", "deny"),
        (f"make build && {T} apply -auto-approve", "deny"),
        (f"{K} apply -f a.yaml", "confirm"),
        (f"{K} exec -it pod -- sh", "confirm"),
        ("env FOO=1 helm upgrade rel chart", "confirm"),
    ],
)
def test_commands_in_command_position_still_match(pol, cmd, outcome):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == outcome, cmd


def test_unbalanced_quote_falls_back_to_the_whole_line(pol):
    assert evaluate(pol, "Bash", {"command": f'{K} delete pod "x'}).outcome == "deny"
    assert evaluate(pol, "Bash", {"command": f'gh issue create --body "{K} exec'}).outcome == "confirm"


def test_program_rule_names_the_program(pol):
    rule = next(r for r in pol["bash_confirm"] if r.get("program") == K)
    assert match_program_rule(rule, f"sudo {K} apply -f x") == K
    assert match_program_rule(rule, f'gh issue create --body "{K} apply"') is None
    d = evaluate(pol, "Bash", {"command": f"{K} --context prod apply -f x"})
    assert d.findings[0]["finding"] == f"run {K} (cluster mutation in production) @ context=prod"
