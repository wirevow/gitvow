"""0.18: the observe tier, session-scoped answers to immediate confirms, and edits outside the repository.

All three exist because a replay of three engineers' real sessions through the default policy priced the gate at
19 asks per engineer per week, two thirds of them one immediate confirm (`git push`) that could never earn a rule,
and found 28% of edits landing in a different checkout than the session's own.
"""

import json
import subprocess
from pathlib import Path

from gitvow import decisions as dec
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.policy import DEFAULT_POLICY_PATH, evaluate
from tests.conftest import git


def _policy(repo, **changes):
    pol = json.loads(Path(DEFAULT_POLICY_PATH).read_text())
    for k, v in changes.items():
        if isinstance(v, dict) and isinstance(pol.get(k), dict):
            pol[k].update(v)
        else:
            pol[k] = v
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(pol))
    return pol


def _hooks(repo):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")


# --------------------------------------------------------------------------------------------- observe tier


def test_observe_rule_records_without_asking(repo, home, payload, transcript):
    pol = _policy(
        repo,
        path_confirm=[{"pattern": r"(^|/)Dockerfile$", "reason": "edits a container build", "when": "observe"}],
        bash_confirm=[{"program": "aws", "verbs": [r"s3\s+cp"], "reason": "AWS write", "when": "observe"}],
    )
    d = evaluate(pol, "Edit", {"file_path": str(repo / "Dockerfile")}, str(repo))
    assert d.outcome == "confirm" and d.observed and not d.deferred and not d.blocks and d.findings[0]["observe"]
    session_start(payload("SessionStart"), str(home))
    assert pre_tool_use(
        payload("PreToolUse", "Edit", {"file_path": str(repo / "Dockerfile")}, transcript), str(home)
    ) == (0, "")
    assert pre_tool_use(
        payload("PreToolUse", "Bash", {"command": "aws --profile p s3 cp a s3://b/"}, transcript), str(home)
    ) == (0, "")
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert log.count('"kind": "observed"') == 2 and '"kind": "finding"' not in log
    # nothing is owed, so no card: the commit goes straight through, in open and in strict mode alike
    assert dec.undecided(str(repo)) == [] and len(dec.observed_open(str(repo))) == 2
    commit = payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript)
    assert pre_tool_use(commit, str(home)) == (0, "")
    assert "Recorded, not asked: 2 observe-tier findings" in dec.card(str(repo), for_agent=False)
    _hooks(repo)
    (repo / "a.txt").write_text("obs\n")
    git(repo, "commit", "-qam", "agent commit with observed findings")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Observed: edit Dockerfile" in body and "Gitvow-Observed: run aws (AWS write)" in body
    assert "Gitvow-Open" not in body
    code, _ = post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 7 and [d["answer"] for d in note["decisions"]] == ["observed", "observed"]
    # observed is not debt and not precedent
    assert dec.open_debt(str(repo)) == []
    assert [o["finding"] for o in dec.observed(str(repo))] == ["edit Dockerfile", "run aws (AWS write)"]
    assert dec.history_all(str(repo)) == []
    # strict mode does not block on observed findings either
    _policy(repo, decisions={"mode": "strict"}, path_confirm=pol["path_confirm"], bash_confirm=pol["bash_confirm"])
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(repo / "Dockerfile")}, transcript), str(home))
    (repo / "a.txt").write_text("obs2\n")
    r = subprocess.run(["git", "commit", "-qam", "strict with observed"], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0 and "Gitvow-Observed: edit Dockerfile" in git(repo, "log", "-1", "--format=%B")


def test_mixed_observe_and_commit_findings_still_reach_the_card(repo, home, payload, transcript):
    pol = _policy(
        repo,
        path_confirm=[{"pattern": r"(^|/)Dockerfile$", "reason": "container build", "when": "observe"}]
        + json.loads(Path(DEFAULT_POLICY_PATH).read_text())["path_confirm"],
    )
    d = evaluate(pol, "Edit", {"file_path": str(repo / "core/authz_rules.go")}, str(repo))
    assert d.deferred and not d.observed
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(repo / "Dockerfile")}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(repo / "core/authz_rules.go")}, transcript), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert (
        code == 2 and "before this commit: 1 finding" in msg and "Dockerfile" not in msg.split("Recorded, not asked")[0]
    )
    assert "Recorded, not asked: 1 observe-tier finding" in msg


# ------------------------------------------------------------------------------- session-scoped answers


def test_immediate_confirm_is_answered_once_per_session(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    push = payload("PreToolUse", "Bash", {"command": "git push origin feat"}, transcript)
    code, msg = pre_tool_use(push, str(home))
    assert code == 2 and msg.startswith("CONFIRMATION REQUIRED (pushing to a remote)")
    assert "gitvow decide 1 accept --scope session" in msg and "gitvow decide 1 decline" in msg
    fs = dec.open_findings(str(repo))
    assert fs[0]["finding"] == "run git (pushing to a remote)" and fs[0]["immediate"] and fs[0]["decision"] is None
    # asked and never answered: not on the card, not owed
    assert dec.undecided(str(repo)) == []
    assert "No open findings" in dec.card(str(repo), for_agent=False)
    # asked again before anyone answers: still asked
    code, _ = pre_tool_use(push, str(home))
    assert code == 2 and len(dec.open_findings(str(repo))) == 1
    # the person says yes; the agent records it; the same question is not asked again this session
    dec.decide(str(repo), "1", "accept", {}, scope="session", reason="release branch")
    assert pre_tool_use(push, str(home)) == (0, "")
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push origin other"}, transcript), str(home)) == (
        0,
        "",
    )
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert log.count('"kind": "allowed_by_session_answer"') == 2
    # the answer rides on the next commit as a scoped decision, and a scoped decision is never precedent
    _hooks(repo)
    (repo / "a.txt").write_text("p\n")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    git(repo, "commit", "-qam", "after the push was allowed")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Accepted: run git (pushing to a remote) by t scope=session: release branch" in body
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    from gitvow.rules import derive

    assert derive(str(repo), {})["proposals"] == [] and derive(str(repo), {})["rules"] == []
    # ...and it survives the commit that carried it: the session is still the session
    assert pre_tool_use(push, str(home)) == (0, "")
    # a new session starts clean
    session_start({**payload("SessionStart"), "session_id": "sess-2"}, str(home))
    code, _ = pre_tool_use({**push, "session_id": "sess-2"}, str(home))
    assert code == 2


def test_declined_immediate_confirm_stays_blocked_for_the_session(repo, home, payload, transcript):
    session_start(payload("SessionStart"), str(home))
    push = payload("PreToolUse", "Bash", {"command": "git push origin feat"}, transcript)
    pre_tool_use(push, str(home))
    dec.decide(str(repo), "1", "decline", {}, reason="not from this branch")
    code, msg = pre_tool_use(push, str(home))
    assert code == 2 and msg.startswith("BLOCKED:") and "declined by t this session" in msg
    _hooks(repo)
    (repo / "a.txt").write_text("d\n")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    git(repo, "commit", "-qam", "carries the decline")
    assert "Gitvow-Declined: run git (pushing to a remote) by t: not from this branch" in git(
        repo, "log", "-1", "--format=%B"
    )


def test_the_agents_own_approve_button_is_recorded_as_the_sessions_answer(repo, home, payload, transcript):
    """Cursor and Copilot deliver the confirm as a question; a click on yes runs the command with no second pass
    through the gate. PostToolUse only fires for calls that ran, so it is the proof a person approved."""
    session_start(payload("SessionStart"), str(home))
    push = payload("PreToolUse", "Bash", {"command": "git push origin feat"}, transcript)
    assert pre_tool_use(push, str(home))[0] == 2
    code, _msg = post_tool_use(
        payload("PostToolUse", "Bash", {"command": "git push origin feat"}, transcript), str(home)
    )
    assert code == 0
    d = dec.open_findings(str(repo))[0]["decision"]
    assert (
        d["answer"] == "accepted"
        and d["scope"] == "session"
        and d["note"] == "approved at the agent's prompt"
        and d["by"] == "t"
    )
    assert '"kind": "prompt_approval_recorded"' in (repo / ".git" / "gitvow-hooks.log").read_text()
    assert pre_tool_use(push, str(home)) == (0, "")
    # recorded once: a second run does not add a second decision
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git push origin feat"}, transcript), str(home))
    assert len(dec.open_findings(str(repo))) == 1


def test_session_scope_can_be_switched_off(repo, home, payload, transcript):
    _policy(repo, decisions={"session_scope": False})
    session_start(payload("SessionStart"), str(home))
    push = payload("PreToolUse", "Bash", {"command": "git push origin feat"}, transcript)
    code, msg = pre_tool_use(push, str(home))
    assert code == 2 and "gitvow decide" not in msg  # the pre-0.18 message: ask every time
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git push origin feat"}, transcript), str(home))
    assert dec.open_findings(str(repo))[0]["decision"] is None  # nothing recorded on the harness's behalf
    assert pre_tool_use(push, str(home))[0] == 2


# --------------------------------------------------------------------------- edits outside the repository


def test_edits_in_another_checkout_are_counted_and_named(repo, home, payload, transcript, tmp_path):
    other = tmp_path / "other-repo"
    other.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    (other / "svc").mkdir()
    (other / "svc" / "main.go").write_text("package main\n")
    session_start(payload("SessionStart"), str(home))
    post_tool_use(payload("PostToolUse", "Edit", {"file_path": str(other / "svc" / "main.go")}, transcript), str(home))
    post_tool_use(payload("PostToolUse", "Edit", {"file_path": str(other / "svc" / "main.go")}, transcript), str(home))
    (repo / "here.txt").write_text("x\n")
    post_tool_use(payload("PostToolUse", "Edit", {"file_path": str(repo / "here.txt")}, transcript), str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["edits_elsewhere"] == {"other-repo": {"count": 2, "paths": ["svc/main.go"]}}
    assert "here.txt" in st["agent_blobs"] and "svc/main.go" not in st["agent_blobs"]
    card = dec.card(str(repo), for_agent=False)
    assert "Edits outside this repository: 2 in other-repo" in card and "gated by that repository's own policy" in card
    _hooks(repo)
    git(repo, "add", "here.txt")
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    git(repo, "commit", "-qm", "with an edit elsewhere")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["edits_outside_repository"] == {"other-repo": {"count": 2, "paths": ["svc/main.go"]}}
    # a new session starts with a clean count
    session_start({**payload("SessionStart"), "session_id": "sess-2"}, str(home))
    assert json.loads((repo / ".git" / "gitvow-session.json").read_text())["edits_elsewhere"] == {}
