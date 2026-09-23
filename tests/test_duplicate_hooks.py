"""0.28.5: the same hook event arriving twice (user-scope and repository-scope settings) is handled once."""

import json

from gitvow.hooks import post_tool_use, pre_tool_use, session_start


def test_second_arrival_of_the_same_tool_use_is_silent(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    (repo / "values" / "production-in").mkdir(parents=True)
    target = repo / "values" / "production-in" / "app.yaml"
    target.write_text("replicas: 1\n")
    p = {**payload("PreToolUse", "Edit", {"file_path": str(target)}), "tool_use_id": "toolu_1"}
    assert pre_tool_use(p, str(home)) == (0, "")
    assert pre_tool_use(p, str(home)) == (0, "")  # the duplicate
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert len(st["findings"]) == 1
    assert (repo / ".git" / "gitvow-hooks.log").read_text().count('"kind": "finding"') == 1
    q = {**payload("PostToolUse", "Edit", {"file_path": str(target)}), "tool_use_id": "toolu_1"}
    post_tool_use(q, str(home))
    post_tool_use(q, str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["snapshots"] == 1
    # the commit call arriving twice shows the card once; its duplicate is silent
    c = {**payload("PreToolUse", "Bash", {"command": "git commit -m x"}), "tool_use_id": "toolu_2"}
    code, msg = pre_tool_use(c, str(home))
    assert code == 2 and "values/production-in/app.yaml" in msg
    assert pre_tool_use(c, str(home)) == (0, "")
    assert (repo / ".git" / "gitvow-hooks.log").read_text().count('"kind": "card"') == 1
    # once the finding is answered, a commit call arriving twice counts one step
    from gitvow import decisions as dec
    from gitvow.policy import load_policy

    dec.decide(str(repo), "1", "accept", load_policy(str(repo), str(home)), reason="ok", rules=[])
    c2 = {**payload("PreToolUse", "Bash", {"command": "git commit -m y"}), "tool_use_id": "toolu_4"}
    pre_tool_use(c2, str(home))
    pre_tool_use(c2, str(home))
    assert json.loads((repo / ".git" / "gitvow-session.json").read_text())["steps"] == 1


def test_a_blocked_first_arrival_stays_blocked_and_the_duplicate_is_silent(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    p = {**payload("PreToolUse", "Bash", {"command": "git reset --hard HEAD~1"}), "tool_use_id": "toolu_3"}
    code, msg = pre_tool_use(p, str(home))
    assert code == 2 and "BLOCKED" in msg
    assert pre_tool_use(p, str(home)) == (0, "")  # the agent already has the block from the first hook


def test_events_without_a_tool_use_id_are_not_deduplicated(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    c = payload("PreToolUse", "Bash", {"command": "git commit -m x"})
    pre_tool_use(c, str(home))
    pre_tool_use(c, str(home))
    assert json.loads((repo / ".git" / "gitvow-session.json").read_text())["steps"] == 2
