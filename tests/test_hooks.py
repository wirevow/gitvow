import json
import os
import time

from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git


def test_session_start_records_state(repo, home, payload):
    assert session_start(payload("SessionStart"), str(home)) == (0, "")
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["session_id"] == "sess-1" and st["steps"] == 0


def test_pre_tool_use_blocks_and_logs(repo, home, payload):
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push --force"}), str(home))
    assert code == 2 and "BLOCKED" in msg
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "blocked"' in log and "force push" in log


def test_pre_tool_use_fails_closed_without_policy(repo, home, payload, monkeypatch):
    import gitvow.policy as pm

    monkeypatch.setattr(pm, "DEFAULT_POLICY_PATH", "/nonexistent/policy.json")
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "ls"}), str(home))
    assert code == 2 and "could not be loaded" in msg


def test_commit_flow_trailer_note_and_attribution(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))[0] == 0
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("changed\n")
    git(repo, "commit", "-qam", "feature")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: sess-1" in body and "Gitvow-Step: 1" in body
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "session note attached" in msg
    assert git(repo, "notes", "--ref=sessions", "show", "HEAD") == ""  # 0.1 shared ref no longer written
    note = git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD")
    assert note.startswith("gitvow-session")
    data = json.loads(note.split("\n", 1)[1])
    assert data["schema"] == 2 and data["step"] == 1 and data["tools_used"] == ["Bash", "Edit"]
    assert "[github-token]" in data["last_stated_plan"] and "[email:" in data["last_stated_plan"]
    assert "ghp_" not in note and "ops@example.com" not in note
    att = data["attribution"]
    assert att["files_in_commit"] == 1 and att["touched_by_agent"] == 1
    assert att["agent_share"] is None  # file known from the transcript only: no agent blob, so lines are not attributed
    assert att["files"][0]["agent_wrote"] is True and "agent_blob" not in att["files"][0]


def test_human_commit_has_no_trailer(repo, home, payload):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("h\n")
    git(repo, "commit", "-qam", "human, no session at all")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")
    session_start(payload("SessionStart"), str(home))  # session active, but the agent did not run git commit
    (repo / "a.txt").write_text("h2\n")
    git(repo, "commit", "-qam", "human, alongside a live session")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    st["pending_commit"] = 1  # a stale flag from long ago
    (repo / ".git" / "gitvow-session.json").write_text(json.dumps(st))
    (repo / "a.txt").write_text("h3\n")
    git(repo, "commit", "-qam", "human, stale flag")
    assert "Gitvow-Session" not in git(repo, "log", "-1", "--format=%B")


def test_pending_flag_is_cleared_after_note_and_no_note_without_trailer(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert "pending_commit" in json.loads((repo / ".git" / "gitvow-session.json").read_text())
    # the agent's commit failed (nothing to commit); PostToolUse still runs
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    assert (code, msg) == (0, "") and git(repo, "for-each-ref", "refs/notes/") == ""
    assert "pending_commit" not in json.loads((repo / ".git" / "gitvow-session.json").read_text())


def test_git_hook_chains_to_repo_hook(repo, home):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    local = repo / ".git" / "hooks" / "prepare-commit-msg"
    local.write_text('#!/bin/sh\necho LOCAL >> "$1"\n')
    os.chmod(local, 0o700)
    (repo / ".git" / "gitvow-session.json").write_text(
        json.dumps({"session_id": "s", "steps": 2, "pending_commit": time.time()})
    )
    (repo / "a.txt").write_text("c\n")
    git(repo, "commit", "-qam", "chain")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: s" in body and "LOCAL" in body


def test_stop_writes_ledger_outside_repo(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    stop(payload("Stop", transcript=transcript), str(home))
    led = home / ".gitvow" / "ledger" / "sess-1.json"
    rec = json.loads((led).read_text())
    assert rec["repo"] == str(repo) and rec["tool_calls"][0]["arg"] == "grep -r password=[redacted] config/"
    assert not (repo / ".gitvow").exists()


def test_post_tool_use_ignores_non_commits(repo, home, payload):
    assert post_tool_use(payload("PostToolUse", "Bash", {"command": "ls"}), str(home)) == (0, "")


def _commit_hooked(repo, home):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")


def test_line_attribution_agent_then_human_edit(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    target = repo / "svc.py"
    target.write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")  # what the agent wrote
    post_tool_use(payload("PostToolUse", "Write", {"file_path": str(target)}), str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert "svc.py" in st["agent_blobs"]
    # a human reworks one function before committing
    target.write_text("def a():\n    return 1\n\ndef b():\n    return 42  # reviewed\n")
    (repo / "other.txt").write_text("human only\n")
    git(repo, "add", "-A")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    _commit_hooked(repo, home)
    git(repo, "commit", "-qm", "mixed")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    data = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    att = data["attribution"]
    assert att["files_in_commit"] == 2 and att["touched_by_agent"] == 1
    assert att["lines_added_in_commit"] == 6 and att["lines_changed_by_human_after_agent"] == 2
    assert att["agent_share"] == round(4 / 6, 2)
    svc = next(f for f in att["files"] if f["path"] == "svc.py")
    assert svc["agent_wrote"] and svc["human_lines_added"] == 1 and svc["human_lines_removed"] == 1
    assert svc["agent_blob"] != svc["committed_blob"]
    other = next(f for f in att["files"] if f["path"] == "other.txt")
    assert other["agent_wrote"] is False and "agent_blob" not in other
    assert data["files_written_by_agent_this_session"] == ["svc.py"]


def test_attribution_untouched_agent_file_has_full_share(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    (repo / "a.txt").write_text("x\ny\n")
    post_tool_use(payload("PostToolUse", "Edit", {"file_path": "a.txt"}), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    _commit_hooked(repo, home)
    git(repo, "commit", "-qam", "agent only")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}), str(home))
    att = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])["attribution"]
    assert att["agent_share"] == 1.0 and att["lines_changed_by_human_after_agent"] == 0


def test_edit_outside_repo_is_ignored(repo, home, payload, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x\n")
    session_start(payload("SessionStart"), str(home))
    post_tool_use(payload("PostToolUse", "Write", {"file_path": str(outside)}), str(home))
    assert json.loads((repo / ".git" / "gitvow-session.json").read_text())["agent_blobs"] == {}


def test_two_sessions_write_separate_refs(repo, home, payload):
    _commit_hooked(repo, home)
    for sid in ("s-one", "s-two"):
        p = payload("SessionStart")
        p["session_id"] = sid
        session_start(p, str(home))
        pre_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))
        (repo / "a.txt").write_text(sid + "\n")
        git(repo, "commit", "-qam", sid)
        post_tool_use({**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}, str(home))
    refs = git(repo, "for-each-ref", "--format=%(refname)", "refs/notes/gitvow/").split()
    assert refs == ["refs/notes/gitvow/s-one", "refs/notes/gitvow/s-two"]
    assert "Gitvow-Session: s-two" in git(repo, "log", "-1", "--format=%B")


def test_custom_rules_apply_to_note_and_ledger(repo, home, payload, transcript):
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "redact-rules.json").write_text(
        json.dumps([{"pattern": "trailers", "replacement": "[repo-rule]"}])
    )
    (home / ".gitvow").mkdir(parents=True)
    (home / ".gitvow" / "redact-rules.json").write_text(
        json.dumps({"rules": [{"pattern": "Plan", "replacement": "[home-rule]"}]})
    )
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    _commit_hooked(repo, home)
    (repo / "a.txt").write_text("r\n")
    git(repo, "commit", "-qam", "rules")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    plan = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])["last_stated_plan"]
    assert "[repo-rule]" in plan and "[home-rule]" in plan
    stop(payload("Stop", transcript=transcript), str(home))
    assert "[repo-rule]" in json.loads((home / ".gitvow" / "ledger" / "sess-1.json").read_text())["last_stated_plan"]


def test_invalid_rules_fail_closed_nothing_written(repo, home, payload, transcript):
    (repo / ".gitvow").mkdir()
    (repo / ".gitvow" / "redact-rules.json").write_text(json.dumps([{"pattern": "(", "replacement": "x"}]))
    session_start(payload("SessionStart"), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push -f"}), str(home))
    assert code == 2  # the gate still works
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "nothing written" in msg
    _commit_hooked(repo, home)
    (repo / "a.txt").write_text("q\n")
    git(repo, "commit", "-qam", "bad rules")
    code, msg = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0 and "nothing written" in msg
    assert git(repo, "for-each-ref", "refs/notes/") == ""
    code, msg = stop(payload("Stop", transcript=transcript), str(home))
    assert code == 0 and not (home / ".gitvow" / "ledger").exists()
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "redaction_unavailable"' in log
    assert "detail" not in [k for ln in log.splitlines() for k in json.loads(ln) if json.loads(ln)["kind"] == "allowed"]
