import json
from pathlib import Path

from gitvow.collect import collect, summarize
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop
from gitvow.install import _write_git_hook
from tests.conftest import git


def test_collect_and_summarize(repo, home, payload, transcript, tmp_path):
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push -f"}), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    (repo / "a.txt").write_text("z\n")
    git(repo, "commit", "-qam", "c")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    stop(payload("Stop", transcript=transcript), str(home))
    w = collect(str(home), str(tmp_path / "out"))
    s = summarize(w)
    assert s["sessions"] == 1 and s["commits_with_trailers"] == 1 and s["notes_attached"] == 1
    assert s["hook_decisions"]["blocked"] == 1 and s["gate_fired_on"][0]["reason"] == "force push"
    assert "ghp_" not in Path(f"{w}/SUMMARY.txt").read_text()
    led = json.loads(next((tmp_path / "out").rglob("sess-1.json")).read_text())
    assert "hunter2secret" not in json.dumps(led)
