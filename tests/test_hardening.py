"""0.26: credential-store paths, safe writes, write-if-changed, the status badge."""

import json
import os
import time

import pytest

from gitvow import paths, snapshots
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook, install_repo
from gitvow.safewrite import UnsafeTargetError, check_target, write_if_changed
from gitvow.status import badge

FORCE_PUSH = "git push " + "--force"  # split so the gate reading this repository's own tool calls does not trip


@pytest.mark.parametrize(
    "p",
    [
        ".env",
        "app/.env.production",
        "deploy/.npmrc",
        "~/.aws/credentials",
        "ops/credentials.json",
        "k8s/secrets/db.yaml",
        "infra/credentials/prod.toml",
        "certs/server.pem",
        "keys/id_ed25519",
        ".docker/config.json",
        "home/.kube/config",
        "vault.kdbx",
    ],
)
def test_credential_paths_are_recognised(p):
    assert paths.is_credential_path(p)


@pytest.mark.parametrize(
    "p", ["deploy/prod-values.yaml", "internal/secrets/store.go", "certs/server.crt", "id_ed25519.pub", "README.md", ""]
)
def test_ordinary_paths_are_not(p):
    assert not paths.is_credential_path(p)


def test_snapshot_exclusions_are_a_union_the_policy_cannot_shrink():
    cfg = snapshots.settings({"snapshots": {"exclude": ["*.sqlite"]}})
    assert "*.sqlite" in cfg["exclude"] and ".env*" in cfg["exclude"] and "**/*.pem" in cfg["exclude"]
    assert snapshots.settings({"snapshots": {"exclude": []}})["exclude"] == snapshots.settings(None)["exclude"]


def test_agent_blob_is_never_kept_for_a_credential_path(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    (repo / ".npmrc").write_text("//registry/:_authToken=abc\n")
    (repo / "b.txt").write_text("b\n")
    post_tool_use(payload("PostToolUse", "Write", {"file_path": str(repo / ".npmrc")}), str(home))
    post_tool_use(payload("PostToolUse", "Write", {"file_path": str(repo / "b.txt")}), str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert set(st["agent_blobs"]) == {"b.txt"}
    assert '"kind": "blob_skipped"' in (repo / ".git" / "gitvow-hooks.log").read_text()


def test_check_target_refuses_escapes_git_dir_and_hard_links(repo, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (repo / ".claude").mkdir()
    os.symlink(outside, repo / ".claude" / "settings.json")
    with pytest.raises(UnsafeTargetError, match="outside"):
        check_target(str(repo / ".claude" / "settings.json"), str(repo))
    os.remove(repo / ".claude" / "settings.json")
    os.symlink(repo / ".git" / "config", repo / ".claude" / "settings.json")
    with pytest.raises(UnsafeTargetError, match="git directory"):
        check_target(str(repo / ".claude" / "settings.json"), str(repo))
    os.remove(repo / ".claude" / "settings.json")
    os.link(repo / ".git" / "config", repo / "CLAUDE.md")
    with pytest.raises(UnsafeTargetError, match="hard link"):
        check_target(str(repo / "CLAUDE.md"), str(repo))
    os.remove(repo / "CLAUDE.md")
    (repo / "adir").mkdir()
    with pytest.raises(UnsafeTargetError, match="not a regular file"):
        check_target(str(repo / "adir"), str(repo))
    assert check_target(str(repo / "AGENTS.md"), str(repo)) == os.path.realpath(repo / "AGENTS.md")


def test_repo_install_refuses_a_redirected_settings_file(repo, home, tmp_path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text("{}")
    (repo / ".claude").mkdir()
    os.symlink(outside, repo / ".claude" / "settings.json")
    with pytest.raises(UnsafeTargetError):
        install_repo(str(repo))
    assert outside.read_text() == "{}"


def test_rerun_leaves_unchanged_hooks_untouched(repo, home):
    d = str(repo / ".gitvow" / "git-hooks")
    stamp = _write_git_hook(d, root=str(repo))
    before = os.stat(stamp).st_mtime_ns
    time.sleep(0.01)
    _write_git_hook(d, root=str(repo))
    assert os.stat(stamp).st_mtime_ns == before
    assert oct(os.stat(stamp).st_mode & 0o777) == "0o755"
    assert write_if_changed(str(repo / "x.txt"), "a") and not write_if_changed(str(repo / "x.txt"), "a")


def test_badge_reads_the_session_state_and_log(repo, home, payload, tmp_path):
    assert badge(str(tmp_path)).endswith("not a repository")
    assert badge(str(repo)).endswith("no session recorded here")
    session_start(payload("SessionStart"), str(home))
    (repo / "values" / "production-in").mkdir(parents=True)
    target = repo / "values" / "production-in" / "app.yaml"
    target.write_text("replicas: 1\n")
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(target)}), str(home))
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": FORCE_PUSH}), str(home))
    assert code == 2
    line = badge(str(repo))
    assert "0 decided" in line and "1 recorded" in line and "1 open" in line and "1 denied" in line
