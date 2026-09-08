import subprocess

import pytest


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@test")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    _git(r, "add", "a.txt")
    _git(r, "commit", "-qm", "init")
    return r


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    # isolate git global config so tests never touch the developer's real one
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(h / ".gitconfig"))
    (h / ".gitconfig").write_text("")
    return h


@pytest.fixture
def payload(repo):
    def make(event, tool="", tool_input=None, transcript=""):
        return {
            "session_id": "sess-1",
            "transcript_path": transcript,
            "cwd": str(repo),
            "hook_event_name": event,
            "tool_name": tool,
            "tool_input": tool_input or {},
        }

    return make


@pytest.fixture
def transcript(tmp_path):
    p = tmp_path / "t.jsonl"
    lines = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Plan: add trailers. Contact ops@example.com; token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 must not leak.",
                    },
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "grep -r password=hunter2secret config/"},
                    },
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "a.txt"}}],
            },
        },
        {"type": "user", "message": {"role": "user", "content": "ok"}},
    ]
    import json

    p.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return str(p)


git = _git
