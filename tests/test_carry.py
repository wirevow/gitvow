"""0.29: session notes carried onto a squash commit, and readers that find a note without the trailer."""

from gitvow import decisions as dec
from gitvow import recall
from gitvow.carry import carry, render
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.notes import find_note
from gitvow.policy import load_policy
from gitvow.report import build
from tests.conftest import git


def _agent_commit(repo, home, payload, transcript, sid, filename, decide=False):
    p = payload("SessionStart")
    p["session_id"] = sid
    session_start(p, str(home))
    if decide:
        (repo / "values" / "production-in").mkdir(parents=True, exist_ok=True)
        (repo / "values" / "production-in" / filename).write_text(f"{sid}\n")
        pre_tool_use(
            {**p, "tool_name": "Edit", "tool_input": {"file_path": str(repo / "values" / "production-in" / filename)}},
            str(home),
        )
        dec.decide(str(repo), "1", "accept", load_policy(str(repo), str(home)), reason="reviewed", rules=[])
    else:
        (repo / filename).write_text(f"{sid}\n")
    cmd = {**p, "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "transcript_path": transcript}
    pre_tool_use(cmd, str(home))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"work by {sid}")
    post_tool_use(cmd, str(home))
    return git(repo, "rev-parse", "HEAD")


def test_carry_onto_a_squash_commit_and_readers_find_it_without_the_trailer(repo, home, payload, transcript):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-qb", "feature")
    _agent_commit(repo, home, payload, transcript, "s-one", "one.txt")
    c2 = _agent_commit(repo, home, payload, transcript, "s-two", "prod.yaml", decide=True)
    assert "Gitvow-Accepted" in git(repo, "log", "-1", "--format=%B", c2)
    head = git(repo, "rev-parse", "HEAD")
    # the squash, with a message a person rewrote: no trailers survive
    git(repo, "checkout", "-q", "-")
    git(repo, "merge", "--squash", "-q", "feature")
    git(repo, "commit", "-qm", "Feature, squashed by a person who rewrote the message")
    squash = git(repo, "rev-parse", "HEAD")
    assert "Gitvow" not in git(repo, "log", "-1", "--format=%B", squash)
    assert find_note(str(repo), squash)[0] is None
    r = carry(str(repo), f"{base}..{head}", squash)
    assert r["commits_in_range"] == 2 and {w["ref"] for w in r["notes_written"]} == {"gitvow/s-one", "gitvow/s-two"}
    text = render(r)
    assert "refs/notes/gitvow/s-two: 1 source commit(s), 1 decision(s)" in text and "git push origin" in text
    # the carried note under s-two names its source and keeps the decision
    note, ref = find_note(str(repo), squash, "s-two")
    assert ref == "gitvow/s-two" and note["carried"] and [c["sha"] for c in note["carried_from"]] == [c2]
    assert note["decisions"][0]["answer"] == "accepted"
    # a reader with no session id finds a note by scanning the refs
    note, ref = find_note(str(repo), squash)
    assert note is not None and ref in ("gitvow/s-one", "gitvow/s-two")
    # the report treats the squash commit as agent work, carried, not as a person's commit
    rep = build(str(repo), base, squash)
    entry = rep["commits"][-1]
    assert entry["kind"] == "agent" and entry["carried"] and len(entry["carried_from"]) >= 1
    # why on a file from the squashed range sees the session through the squash commit
    assert "s-one" in recall.why(str(repo), "one.txt") or "s-two" in recall.why(str(repo), "one.txt")
    # idempotent
    r2 = carry(str(repo), f"{base}..{head}", squash)
    assert {w["ref"] for w in r2["notes_written"]} == {"gitvow/s-one", "gitvow/s-two"}


def test_carry_with_nothing_to_carry_says_so(repo):
    base = git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "plain")
    head = git(repo, "rev-parse", "HEAD")
    r = carry(str(repo), f"{base}..{head}", head)
    assert r["notes_written"] == [] and "nothing carried" in render(r)
