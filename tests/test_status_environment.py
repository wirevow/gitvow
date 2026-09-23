"""0.29.1: status checks the environment the git hooks run in."""

import json
import shutil

from gitvow.install import install_repo, install_user
from gitvow.status import build


def _first(checks, needle):
    return next((c for c in checks if needle in c[1]), None)


def test_python3_missing_from_path_is_a_failure(repo, home, monkeypatch):
    install_repo(str(repo))
    monkeypatch.setattr(shutil, "which", lambda name, path=None: None if name == "python3" else "/usr/bin/" + name)
    checks = build(str(repo), str(home))
    c = _first(checks, "python3 is not on PATH")
    assert c and c[0] == "fail" and "fail silently" in c[1]


def test_python3_only_on_a_rich_path_is_a_note(repo, home, monkeypatch):
    install_repo(str(repo))
    real_which = shutil.which

    def which(name, path=None):
        if name == "python3":
            return None if path else "/Users/x/.pyenv/shims/python3"
        return real_which(name, path=path)

    monkeypatch.setattr(shutil, "which", which)
    checks = build(str(repo), str(home))
    c = _first(checks, "not on a minimal one")
    assert c and c[0] == "note" and "Finder" in c[2]


def test_python3_on_a_minimal_path_is_ok(repo, home):
    install_repo(str(repo))
    checks = build(str(repo), str(home))
    c = _first(checks, "python3 reachable from git hooks")
    assert c and c[0] == "ok"


def test_short_commit_window_is_noted(repo, home):
    install_repo(str(repo))
    p = repo / ".gitvow" / "policy.json"
    d = json.loads(p.read_text())
    d["decisions"]["commit_window_seconds"] = 5
    p.write_text(json.dumps(d))
    checks = build(str(repo), str(home))
    c = _first(checks, "commit_window_seconds is 5")
    assert c and c[0] == "note"


def test_both_scopes_installed_is_noted(repo, home, monkeypatch):
    monkeypatch.chdir(repo)
    install_user(str(home))
    install_repo(str(repo))
    checks = build(str(repo), str(home))
    c = _first(checks, "user scope and in this repository")
    assert c and c[0] == "note" and "0.28.5" in c[2]
