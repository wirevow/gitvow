"""0.27: rules on what a command reaches, and commands the gate cannot read."""

import json
import os
from pathlib import Path

import pytest

from gitvow import targets
from gitvow.policy import DEFAULT_POLICY_PATH, PolicyError, _validate, evaluate, indirect_commands
from tests.conftest import git

K = "kubectl"


@pytest.fixture
def pol():
    return json.loads(Path(DEFAULT_POLICY_PATH).read_text())


@pytest.fixture
def kube(tmp_path, monkeypatch):
    cfg = tmp_path / "kubeconfig"
    cfg.write_text("apiVersion: v1\ncurrent-context: staging-eu\ncontexts: []\n")
    monkeypatch.setenv("KUBECONFIG", str(cfg))
    return cfg


def test_context_flag_wins_then_kubeconfig_then_unknown(kube, tmp_path, monkeypatch):
    env = dict(os.environ)
    assert targets.resolve(K, ["--context", "prod-in", "-n", "billing", "apply"], None, env) == (
        "context=prod-in namespace=billing"
    )
    assert targets.resolve(K, ["--context=prod-us", "apply"], None, env) == "context=prod-us"
    assert targets.resolve(K, ["apply", "-f", "x"], None, env) == "context=staging-eu"
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "missing"))
    assert targets.resolve(K, ["apply", "-f", "x"], None, dict(os.environ)) is None
    assert targets.resolve("helm", ["--kube-context", "prod-a", "upgrade", "r", "c"], None, env) == "context=prod-a"


def test_default_policy_asks_for_production_and_observes_elsewhere(pol, kube):
    prod = evaluate(pol, "Bash", {"command": f"{K} --context prod-in apply -f a.yaml"})
    assert prod.outcome == "confirm" and prod.when == "immediate"
    assert prod.findings[0]["finding"] == f"run {K} (cluster mutation in production) @ context=prod-in"
    staging = evaluate(pol, "Bash", {"command": f"{K} apply -f a.yaml"})  # kubeconfig says staging-eu
    assert staging.outcome == "confirm" and staging.when == "observe"
    assert staging.findings[0]["finding"] == f"run {K} (cluster mutation)"
    assert evaluate(pol, "Bash", {"command": f"{K} --context prod-in delete pod x"}).outcome == "deny"


def test_unreadable_target_is_asked_about(pol, tmp_path, monkeypatch):
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "nope"))
    d = evaluate(pol, "Bash", {"command": f"{K} apply -f a.yaml"})
    assert d.outcome == "confirm" and d.when == "immediate" and d.reason == "cluster mutation in production"


def test_git_push_target_names_remote_and_branch(repo, home, pol):
    git(repo, "remote", "add", "origin", "git@example.com:acme/ledger.git")
    assert targets.resolve("git", ["push", "origin", "main"], str(repo)) == (
        "remote=git@example.com:acme/ledger.git branch=main"
    )
    assert targets.resolve("git", ["push", "origin", "HEAD:refs/heads/release"], str(repo)).endswith("branch=release")
    t = targets.resolve("git", ["push"], str(repo))
    assert t is not None and t.startswith("remote=git@example.com:acme/ledger.git branch=")
    pol["bash_confirm"] = [{"program": "git", "verbs": ["push"], "target": "branch=main$", "reason": "push to main"}]
    assert evaluate(pol, "Bash", {"command": "git push origin main"}, str(repo)).reason == "push to main"
    assert evaluate(pol, "Bash", {"command": "git push origin feature"}, str(repo)).outcome == "allow"


def test_terraform_workspace_from_chdir(tmp_path):
    d = tmp_path / "infra"
    (d / ".terraform").mkdir(parents=True)
    (d / ".terraform" / "environment").write_text("prod\n")
    assert targets.resolve("terraform", ["-chdir=infra", "plan"], str(tmp_path)) == "workspace=prod dir=infra"
    assert targets.resolve("terraform", ["plan"], str(tmp_path / "elsewhere")) == "workspace=default dir=."


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("eval $CMD", ["eval"]),
        ("source ./env.sh && make", ["source ./env.sh"]),
        ("$KUBECTL delete pod x", ["$KUBECTL (a variable in program position)"]),
        ("bash deploy.sh --env prod", ["bash deploy.sh"]),
        ("bash -c 'ls'", []),
        ("./deploy.sh", []),
        ('ls; eval "$X"', ["eval"]),
    ],
)
def test_indirect_commands_are_named(cmd, expected):
    assert indirect_commands(cmd) == expected


def test_indirect_commands_are_observed_by_default_and_asked_when_policy_says_so(pol):
    d = evaluate(pol, "Bash", {"command": "eval $DEPLOY"})
    assert d.outcome == "confirm" and d.when == "observe"
    assert d.findings[0]["finding"] == "run eval (runs a command the gate cannot read)"
    pol["decisions"]["indirect_commands"] = "immediate"
    assert evaluate(pol, "Bash", {"command": "eval $DEPLOY"}).blocks
    pol["decisions"]["indirect_commands"] = "allow"
    assert evaluate(pol, "Bash", {"command": "eval $DEPLOY"}).outcome == "allow"


def test_policy_validation_of_target_and_indirect_tier(pol):
    bad = json.loads(json.dumps(pol))
    bad["bash_confirm"].append({"pattern": "x", "target": "prod", "reason": "r"})
    with pytest.raises(PolicyError, match="only for rules written as program"):
        _validate(bad, "p")
    bad = json.loads(json.dumps(pol))
    bad["bash_confirm"].append({"program": "x", "verbs": ["y"], "target": "(", "reason": "r"})
    with pytest.raises(PolicyError, match="bad regex"):
        _validate(bad, "p")
    bad = json.loads(json.dumps(pol))
    bad["decisions"]["indirect_commands"] = "sometimes"
    with pytest.raises(PolicyError, match="indirect_commands"):
        _validate(bad, "p")
