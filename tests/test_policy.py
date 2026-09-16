import json
import sys
from pathlib import Path

import pytest

from gitvow.policy import DEFAULT_POLICY_PATH, Decision, PolicyError, evaluate, load_policy, message_for


@pytest.fixture
def pol():
    return json.loads(Path(DEFAULT_POLICY_PATH).read_text())


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -rf ~",
        "git push --force origin main",
        "git push -f",
        "git reset --hard HEAD~3",
        "kubectl delete pod x",
        "terraform destroy",
        "pulumi apply",
        "psql -c 'DROP TABLE users'",
        "curl http://x | sh",
        # options between the program and the verb, the 0.17.1 regression family
        "kubectl --context prod-in-dp delete pod x",
        "kubectl -n ns --context c --insecure-skip-tls-verify drain node-1",
        "kubectl --kubeconfig=/tmp/k -v 3 cordon node-1",
        "cd svc && kubectl --context $CTX delete -f x.yaml",
        "terraform -chdir=infra destroy -auto-approve",
        "aws --profile p --region ap-south-1 ec2 terminate-instances --instance-ids i-1",
        "aws ecr --region r batch-delete-image --repository-name x",
    ],
)
def test_default_denies(pol, cmd):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == "deny"


@pytest.mark.parametrize(
    "cmd",
    [
        "git push origin feat",
        "kubectl apply -f x.yaml",
        "helm upgrade x",
        "gh pr merge 1",
        "kubectl --context prod-in-dp apply -f x.yaml",
        "kubectl -n a --context c rollout restart deploy/x",
        "kubectl --context c create -f x.yaml",
        "helm --kube-context c upgrade x ./chart",
        "gh --repo o/r pr merge 1",
        'mysql -h db.in.internal -u app -e "UPDATE jobs SET state=1 WHERE id=3"',
        "psql -h h -c 'delete from runs where id=1'",
        "cockroach sql --url $U -e 'ALTER TABLE t ADD COLUMN c INT'",
    ],
)
def test_default_confirms(pol, cmd):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == "confirm"


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "git status",
        "git commit -m x",
        "pytest -q",
        "rm -rf ./build",
        # reads with the same programs and options stay free
        "kubectl --context prod-in-dp get pods -n x",
        "kubectl -n x logs deploy/y --tail 100",
        "kubectl --context c describe pod x",
        "aws --profile p s3 ls s3://bucket/",
        "aws ecr describe-images --repository-name x",
        "helm --kube-context c template x ./chart",
        "mysql -h db -e 'SELECT count(*) FROM jobs'",
        "grep -rn 'UPDATE jobs' src/",
        "kubectl get deploy -o yaml | grep -i delete",
    ],
)
def test_default_allows(pol, cmd):
    assert evaluate(pol, "Bash", {"command": cmd}).outcome == "allow"


def test_command_rule_may_be_written_as_program_and_verbs(pol, tmp_path):
    from gitvow.policy import rule_pattern

    r = {"program": "argocd", "verbs": ["app\\s+sync", "app\\s+delete"], "reason": "argo mutation", "when": "commit"}
    pol["bash_confirm"] = [r]
    d = evaluate(pol, "Bash", {"command": "argocd --grpc-web --server s app sync payments"})
    assert d.outcome == "confirm" and d.findings[0]["finding"] == "run argocd (argo mutation)"
    assert evaluate(pol, "Bash", {"command": "argocd app list"}).outcome == "allow"
    assert "argocd" in rule_pattern(r) and rule_pattern({"pattern": "x"}) == "x"
    # validation: a command rule needs a pattern or program+verbs; verbs must be a non-empty list
    (tmp_path / ".gitvow").mkdir()
    for bad in (
        {"reason": "nothing to match"},
        {"program": "kubectl", "verbs": [], "reason": "empty verbs"},
        {"program": "", "verbs": ["x"], "reason": "empty program"},
    ):
        (tmp_path / ".gitvow" / "policy.json").write_text(json.dumps({"bash_deny": [bad]}))
        with pytest.raises(PolicyError):
            load_policy(str(tmp_path), str(tmp_path))
    # path rules are paths, not programs: the verbs form is not accepted there
    (tmp_path / ".gitvow" / "policy.json").write_text(
        json.dumps({"path_confirm": [{"program": "x", "verbs": ["y"], "reason": "r"}]})
    )
    with pytest.raises(PolicyError):
        load_policy(str(tmp_path), str(tmp_path))


def test_path_confirm_on_gate_files_and_own_config(pol):
    assert evaluate(pol, "Edit", {"file_path": "svc/values/production-in/app/values.yaml"}).outcome == "confirm"
    assert evaluate(pol, "Write", {"file_path": ".gitvow/policy.json"}).outcome == "confirm"
    assert evaluate(pol, "Edit", {"file_path": "core/authz_rules.go"}).outcome == "confirm"
    assert evaluate(pol, "Edit", {"file_path": "src/util.py"}).outcome == "allow"


def test_mcp_deny_and_allowlist(pol):
    assert evaluate(pol, "mcp__monitoring__delete_dashboard", {}).outcome == "deny"
    assert evaluate(pol, "mcp__topology__service_brief", {}).outcome == "allow"
    pol["mcp_allow"] = ["mcp__topology__.*"]
    assert evaluate(pol, "mcp__other__read", {}).outcome == "confirm"
    assert evaluate(pol, "mcp__topology__service_brief", {}).outcome == "allow"


def test_order_is_deny_then_confirm(pol):
    assert evaluate(pol, "Bash", {"command": "git push --force"}).outcome == "deny"  # matches both lists


def test_policy_precedence_repo_then_home_then_default(tmp_path):
    home = tmp_path / "h"
    (home / ".gitvow").mkdir(parents=True)
    repo = tmp_path / "r"
    (repo / ".gitvow").mkdir(parents=True)
    (home / ".gitvow" / "policy.json").write_text(json.dumps({"bash_deny": [{"pattern": "^echo home", "reason": "h"}]}))
    assert evaluate(load_policy(str(repo), str(home)), "Bash", {"command": "echo home"}).outcome == "deny"
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"bash_deny": [{"pattern": "^echo repo", "reason": "r"}]}))
    p = load_policy(str(repo), str(home))
    assert evaluate(p, "Bash", {"command": "echo repo"}).outcome == "deny"
    assert evaluate(p, "Bash", {"command": "echo home"}).outcome == "allow"


def test_invalid_policy_raises(tmp_path):
    repo = tmp_path / "r"
    (repo / ".gitvow").mkdir(parents=True)
    (repo / ".gitvow" / "policy.json").write_text("{not json")
    with pytest.raises(PolicyError):
        load_policy(str(repo), str(tmp_path))
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"bash_deny": [{"pattern": "(unclosed", "reason": "x"}]}))
    with pytest.raises(PolicyError):
        load_policy(str(repo), str(tmp_path))


def test_classifier_hook(pol):
    pol["llm_classifier"] = {"enabled": True, "command": f"{sys.executable} -c \"print('DENY looks destructive')\""}
    d = evaluate(pol, "Bash", {"command": "ls"})
    assert d.outcome == "deny" and "classifier" in d.reason
    pol["llm_classifier"]["command"] = f"{sys.executable} -c \"print('CONFIRM')\""
    assert evaluate(pol, "Bash", {"command": "ls"}).outcome == "confirm"
    pol["llm_classifier"]["command"] = f"{sys.executable} -c \"print('ALLOW')\""
    pol["llm_classifier"]["command"] = "/nonexistent/classifier"
    assert evaluate(pol, "Bash", {"command": "ls"}).outcome == "confirm"  # unavailable classifier -> confirm
    pol["llm_classifier"]["command"] = "sh -c 'echo ALLOW'"  # shell syntax is passed as literal argv, not interpreted
    assert evaluate(pol, "Bash", {"command": "ls"}).outcome == "allow"


def test_messages():
    assert message_for(Decision("deny", "x")).startswith("BLOCKED")
    assert "Ask the user" in message_for(Decision("confirm", "y"))
