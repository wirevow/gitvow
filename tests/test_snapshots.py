import json
import os

from gitvow import cli
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook, uninstall_repo
from tests.conftest import git


def _edit(repo, home, payload, name, content, tool="Edit"):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content)
    post_tool_use(payload("PostToolUse", tool, {"file_path": str(repo / name)}), str(home))


def test_snapshot_per_edit_with_head_parent_and_exclusions(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    (repo / ".gitignore").write_text("build/\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("bin")
    (repo / ".env").write_text("SECRET=1\n")
    _edit(repo, home, payload, "calc.py", "def add(a, b):\n    return a + b\n")
    _edit(repo, home, payload, "calc.py", "def add(a, b):\n    return a + b\n\ndef sub(a, b):\n    return a - b\n")
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/gitvow/snapshots/").split()
    assert refs == ["refs/gitvow/snapshots/sess-1/1", "refs/gitvow/snapshots/sess-1/2"]
    head = git(repo, "rev-parse", "HEAD")
    assert git(repo, "rev-parse", "refs/gitvow/snapshots/sess-1/1^") == head  # parented on HEAD at the time
    assert git(repo, "show", "refs/gitvow/snapshots/sess-1/1:calc.py") == "def add(a, b):\n    return a + b"
    assert "sub" in git(repo, "show", "refs/gitvow/snapshots/sess-1/2:calc.py")
    tree = git(repo, "ls-tree", "-r", "--name-only", "refs/gitvow/snapshots/sess-1/2")
    assert "calc.py" in tree and ".gitignore" in tree and "build/out.o" not in tree and ".env" not in tree
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text("{}")
    _edit(repo, home, payload, "calc.py", "v3\n")
    assert ".gitvow/policy.json" not in git(repo, "ls-tree", "-r", "--name-only", "refs/gitvow/snapshots/sess-1/3")
    assert git(repo, "status", "--short").count("\n") >= 0  # the real index was never touched
    assert "calc.py" not in git(repo, "diff", "--cached", "--name-only")
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["snapshots"] == 3 and st["last_snapshot"] == "refs/gitvow/snapshots/sess-1/3"
    assert '"kind": "snapshot"' in (repo / ".git" / "gitvow-hooks.log").read_text()


def test_note_names_last_snapshot_and_new_session_restarts_numbering(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    _edit(repo, home, payload, "a.txt", "agent\n")
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    git(repo, "commit", "-qam", "agent commit")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 3 and note["snapshot"] == "refs/gitvow/snapshots/sess-1/1"
    p = payload("SessionStart")
    p["session_id"] = "sess-2"
    session_start(p, str(home))
    (repo / "a.txt").write_text("two\n")
    post_tool_use({**p, "tool_name": "Write", "tool_input": {"file_path": str(repo / "a.txt")}}, str(home))
    assert git(repo, "rev-parse", "--verify", "refs/gitvow/snapshots/sess-2/1")


def test_cli_list_diff_restore_prune(repo, home, payload, monkeypatch, capsys, tmp_path):
    session_start(payload("SessionStart"), str(home))
    _edit(repo, home, payload, "calc.py", "v1\n")
    _edit(repo, home, payload, "calc.py", "v2\n", tool="Write")
    (repo / "calc.py").write_text("human v3\n")
    monkeypatch.chdir(repo)
    assert cli.main(["snapshots"]) == 0
    out = capsys.readouterr().out
    assert "session sess-1   2 snapshots" in out and "Write" in out and "calc.py" in out
    assert cli.main(["diff", "sess", "1"]) == 0
    assert "calc.py" in capsys.readouterr().out
    assert cli.main(["diff", "sess", "1", "--full"]) == 0
    assert "+v1" in capsys.readouterr().out
    dest = tmp_path / "restore-here"
    assert cli.main(["restore", "sess", "1", "--to", str(dest)]) == 0
    assert (dest / "calc.py").read_text() == "v1\n"
    assert (repo / "calc.py").read_text() == "human v3\n"  # the real tree is untouched
    assert cli.main(["restore", "sess", "1", "--to", str(dest)]) == 1  # refuses a non-empty dir
    assert cli.main(["diff", "nope", "1"]) == 1
    assert cli.main(["diff", "sess", "9"]) == 1
    git(repo, "worktree", "remove", "--force", str(dest))
    assert cli.main(["snapshots", "prune", "--older-than", "14d"]) == 0
    assert "pruned 0" in capsys.readouterr().out
    assert cli.main(["snapshots", "prune", "--session", "sess-1"]) == 0
    assert "pruned 2" in capsys.readouterr().out
    assert cli.main(["snapshots"]) == 0 and "no snapshots" in capsys.readouterr().out


def test_retention_disable_and_purge(repo, home, payload):
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"snapshots": {"max_per_session": 2}}))
    session_start(payload("SessionStart"), str(home))
    for i in range(4):
        _edit(repo, home, payload, "a.txt", f"v{i}\n")
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/gitvow/snapshots/").split()
    assert [r.split("/")[-1] for r in refs] == ["3", "4"]
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"snapshots": {"enabled": False}}))
    _edit(repo, home, payload, "a.txt", "v9\n")
    assert len(git(repo, "for-each-ref", "refs/gitvow/snapshots/").split("\n")) == 2
    uninstall_repo(str(repo), purge_snapshots=True)
    assert git(repo, "for-each-ref", "refs/gitvow/snapshots/") == ""


def test_pre_push_does_not_push_snapshots(repo, home, payload, tmp_path):
    remote = tmp_path / "remote.git"
    git(remote.parent, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    session_start(payload("SessionStart"), str(home))
    _edit(repo, home, payload, "a.txt", "x\n")
    git(repo, "notes", "--ref=gitvow/sess-1", "add", "-m", "gitvow-session\n{}", "HEAD")
    git(repo, "push", "-q", "origin", git(repo, "branch", "--show-current"))
    remote_refs = git(remote, "for-each-ref", "--format=%(refname)")
    assert "refs/notes/gitvow/sess-1" in remote_refs and "snapshots" not in remote_refs


def test_invalid_snapshots_policy_fails_closed(repo, home, payload):
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"snapshots": "yes please"}))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "ls"}), str(home))
    assert code == 2 and "snapshots must be an object" in msg


def test_snapshot_in_repo_without_commits(tmp_path, home, payload):
    r = tmp_path / "fresh"
    r.mkdir()
    git(r, "init", "-q")
    (r / "f.txt").write_text("first\n")
    p = payload("SessionStart")
    p["cwd"] = str(r)
    session_start(p, str(home))
    post_tool_use({**p, "tool_name": "Write", "tool_input": {"file_path": str(r / "f.txt")}}, str(home))
    ref = git(r, "for-each-ref", "--format=%(refname)", "refs/gitvow/snapshots/")
    assert ref.endswith("/1") and git(r, "show", f"{ref}:f.txt") == "first"
    assert os.path.exists(r / ".git")
