import json

from gitvow import cli
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git


def _agent_session(repo, home, payload, sid, file, content, subject, transcript=""):
    p = payload("SessionStart", transcript=transcript)
    p["session_id"] = sid
    session_start(p, str(home))
    (repo / file).write_text(content)
    post_tool_use({**p, "tool_name": "Edit", "tool_input": {"file_path": str(repo / file)}}, str(home))
    git(repo, "add", file)
    pre_tool_use(
        {**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "transcript_path": transcript},
        str(home),
    )
    git(repo, "commit", "-qm", subject)
    post_tool_use(
        {**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "transcript_path": transcript},
        str(home),
    )
    stop({**p, "transcript_path": transcript}, str(home))
    return p


def test_why_trace_recall_handoff(repo, home, payload, transcript, monkeypatch, capsys, tmp_path):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    # a transcript whose plan mentions "cache key" for session one
    t2 = tmp_path / "t2.jsonl"
    t2.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Plan: change the cache key to include week start."}],
                },
            }
        )
        + "\n"
    )
    _agent_session(
        repo, home, payload, "sess-one", "calc.py", "def add(a, b):\n    return a + b\n", "Add calc", str(t2)
    )
    # a person edits calc.py and commits without a session
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n\ndef human():\n    return 1\n")
    git(repo, "commit", "-qam", "human adds helper")
    # second agent session touches another file and mentions "export"
    _agent_session(
        repo, home, payload, "sess-two", "orders.py", "route = '/v1/orders/export'\n", "Add export", transcript
    )
    monkeypatch.chdir(repo)

    assert cli.main(["why", "calc.py"]) == 0
    out = capsys.readouterr().out
    assert "1 agent commit, 1 human commit, 1 session" in out
    assert "session sess-one" in out and "cache key" in out and "(person)" in out and "agent share 1.00" in out
    assert "1 snapshot of this file" in out

    assert cli.main(["trace", "calc.py:1-5"]) == 0
    out = capsys.readouterr().out
    assert "agent" in out and "session sess-one" in out and "person" in out
    assert cli.main(["trace", "calc.py"]) == 0
    assert cli.main(["trace", "nope.py"]) == 0  # reports the git error instead of crashing

    assert cli.main(["recall", "cache", "key"]) == 0
    out = capsys.readouterr().out
    assert "1 session mention" in out and "sess-one" in out and "sess-two" not in out
    assert cli.main(["recall", "export"]) == 0
    assert "sess-two" in capsys.readouterr().out
    assert cli.main(["recall", "zzzunlikely"]) == 0
    assert "nothing recorded" in capsys.readouterr().out
    # a session that ended without committing is found through the ledger alone
    p3 = payload("SessionStart", transcript=str(t2))
    p3["session_id"] = "sess-three"
    session_start(p3, str(home))
    pre_tool_use({**p3, "tool_name": "Bash", "tool_input": {"command": "kubectl apply -f x.yaml"}}, str(home))
    stop(p3, str(home))
    assert cli.main(["recall", "week", "start"]) == 0
    out = capsys.readouterr().out
    assert "2 sessions mention" in out and "sess-thr" in out and "(ledger only)" in out
    assert cli.main(["handoff", "--session", "sess-three"]) == 0
    out = capsys.readouterr().out
    assert "Open confirmations: 1" in out and "kubectl apply" in out and "Commits this session: none" in out

    (repo / "orders.py").write_text("route = '/v1/orders/export'\nx = 1\n")  # uncommitted work
    p2 = payload("SessionStart")
    p2["session_id"] = "sess-two"
    session_start(p2, str(home))  # resume session two so handoff defaults to it
    assert cli.main(["handoff"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Handoff · session sess-two") and "Add export" in out and "orders.py" in out
    assert "Uncommitted now: 1 file changed" in out and "last snapshot refs/gitvow/snapshots/sess-two/1" in out
    assert cli.main(["handoff", "--session", "sess-one"]) == 0
    assert "Add calc" in capsys.readouterr().out


def test_handoff_without_session(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert cli.main(["handoff"]) == 0
    assert "no session known" in capsys.readouterr().out
    assert cli.main(["why", "a.txt"]) == 0
    assert "0 agent commits, 1 human commit" in capsys.readouterr().out
