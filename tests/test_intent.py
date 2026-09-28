"""0.30: intent. One line at task start, in the person's words, on every commit of the session and in the note."""

import json

import pytest

from gitvow import cli
from gitvow import intent as im
from gitvow.digest import build, render
from gitvow.hooks import post_tool_use, pre_tool_use, session_start, stop, user_prompt_submit
from gitvow.install import _write_git_hook
from gitvow.policy import DEFAULT_POLICY_PATH, PolicyError, load_policy
from gitvow.serve import call_tool, tool_list
from tests.conftest import git

PROMPT = "Let ops export orders as CSV from the admin page.\nDetails: see the ticket.\n"


def _hooked(repo):
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")


def _commit(repo, home, payload, msg, transcript=""):
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    (repo / "a.txt").write_text(msg + "\n")
    git(repo, "commit", "-qam", msg)
    return post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))


def test_from_prompt_takes_the_first_line_and_skips_what_is_not_an_intent():
    assert im.from_prompt(PROMPT) == "Let ops export orders as CSV from the admin page."
    assert im.from_prompt("/graphify docs") is None  # a slash command
    assert im.from_prompt("!git status") is None
    assert im.from_prompt("go") is None and im.from_prompt("yes please") is None  # a nudge, not a task
    assert im.from_prompt("") is None and im.from_prompt(None) is None
    pasted = '<pasted_content id="x">secret dump\nmore</pasted_content>\nfix the login redirect loop'
    assert im.from_prompt(pasted) == "fix the login redirect loop"
    long = "word " * 80
    assert len(im.from_prompt(long)) == im.MAX_TEXT
    assert im.from_prompt("<system-reminder>ignore</system-reminder> add rate limiting to the export route") == (
        "add rate limiting to the export route"
    )


def test_first_prompt_becomes_the_intent_and_rides_on_every_commit(repo, home, payload, transcript):
    _hooked(repo)
    rc, ctx = session_start(payload("SessionStart"), str(home))
    assert rc == 0 and "first line of the person's first message" in ctx and 'gitvow intent "<their words>"' in ctx
    assert user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home)) == (0, "")
    i = im.current(str(repo))
    assert (
        i["text"] == "Let ops export orders as CSV from the admin page." and i["source"] == "prompt" and i["by"] == "t"
    )
    # only the first message: the second does not replace it
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": "now also add a button for it"}, str(home))
    assert im.current(str(repo))["text"].startswith("Let ops export")
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert log.count('"kind": "intent"') == 1 and "Let ops" not in log  # the log counts; it never holds the words
    code, msg = _commit(repo, home, payload, "one", transcript)
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Session: sess-1" in body
    assert "Gitvow-Intent: Let ops export orders as CSV from the admin page. by t source=prompt" in body
    assert im.parse_trailer(body) == {
        "text": "Let ops export orders as CSV from the admin page.",
        "by": "t",
        "source": "prompt",
    }
    assert code == 0 and "session note attached" in msg
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert (
        note["schema"] == 8
        and note["intent"]["text"].startswith("Let ops export")
        and note["intent"]["source"] == "prompt"
    )
    # the second commit of the session carries it too
    _commit(repo, home, payload, "two", transcript)
    assert "Gitvow-Intent: Let ops export" in git(repo, "log", "-1", "--format=%B")
    # trailers stay one block: git reads them back
    trailers = git(repo, "log", "-1", "--format=%(trailers:key=Gitvow-Intent,valueonly)")
    assert trailers.startswith("Let ops export")
    stop(payload("Stop"), str(home))
    led = json.loads((home / ".gitvow" / "ledger" / "sess-1.json").read_text())
    assert led["intent"]["text"].startswith("Let ops export")
    # a person's terminal commit carries no session and no intent
    (repo / "a.txt").write_text("human\n")
    import time

    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    st["pending_commit"] = time.time() - 100000
    (repo / ".git" / "gitvow-session.json").write_text(json.dumps(st))
    git(repo, "commit", "-qam", "by hand")
    assert "Gitvow-Intent" not in git(repo, "log", "-1", "--format=%B")


def test_a_new_session_starts_without_the_old_intent_and_a_resumed_one_keeps_it(repo, home, payload):
    session_start(payload("SessionStart"), str(home))
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home))
    session_start(payload("SessionStart"), str(home))  # same session id: resumed
    assert im.current(str(repo)) is not None
    _, ctx = session_start(payload("SessionStart"), str(home))
    assert 'gitvow intent for this session: "Let ops export' in ctx
    session_start({**payload("SessionStart"), "session_id": "sess-2"}, str(home))
    assert im.current(str(repo)) is None


def test_stated_intent_by_command_prints_clears_and_redacts(repo, home, payload, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    session_start(payload("SessionStart"), str(home))
    assert cli.main(["intent"]) == 1
    assert "no intent recorded" in capsys.readouterr().out
    rc = cli.main(["intent", "rotate", "the", "token", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", "for", "ops"])
    out = capsys.readouterr()
    assert rc == 0 and out.out.startswith("Gitvow-Intent: rotate the token [") and "ghp_" not in out.out
    assert out.out.rstrip().endswith("for ops by t") and "source=prompt" not in out.out
    assert "every commit the agent makes" in out.err
    i = im.current(str(repo))
    assert i["source"] == "stated" and i["by"] == "t"
    # a stated intent outranks the first message that follows
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home))
    assert im.current(str(repo))["source"] == "stated"
    assert cli.main(["intent"]) == 0 and capsys.readouterr().out.startswith("Gitvow-Intent: rotate")
    assert cli.main(["intent", "--json"]) == 0 and json.loads(capsys.readouterr().out)["source"] == "stated"
    assert cli.main(["intent", "--by", "priya", "ship", "the", "export"]) == 0
    assert im.current(str(repo))["by"] == "priya"
    assert cli.main(["intent", "--clear"]) == 0 and "cleared" in capsys.readouterr().err
    assert im.current(str(repo)) is None
    assert cli.main(["intent", "--clear"]) == 0 and "no intent was recorded" in capsys.readouterr().err


def test_coverage_is_a_word_overlap_shown_on_the_card_and_counted(repo, home, payload, transcript):
    _hooked(repo)
    session_start(payload("SessionStart"), str(home))
    user_prompt_submit(
        {**payload("UserPromptSubmit"), "prompt": "tighten the authz rules before the staging cut"}, str(home)
    )
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Write", {"file_path": ".github/workflows/ci.yml"}, transcript), str(home))
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 2
    assert (
        'Stated intent: "tighten the authz rules before the staging cut" (from the first message of the session).'
        in msg
    )
    assert "context for the answer, not the answer" in msg
    assert "1. edit core/authz_rules.go   [within the stated intent]" in msg
    assert "2. edit .github/workflows/ci.yml\n" in msg  # not covered: no shared word
    fs = json.loads((repo / ".git" / "gitvow-session.json").read_text())["findings"]
    assert [f["intent_covered"] for f in fs] == [True, False]
    log = [json.loads(ln) for ln in (repo / ".git" / "gitvow-hooks.log").read_text().splitlines()]
    card = [e for e in log if e["kind"] == "card"][-1]
    assert card["intent"] is True and card["intent_covered"] == 1 and card["findings"] == 2
    from gitvow import decisions as dec

    dec.decide(str(repo), "1", "accept", {}, reason="in scope", user_turns=2)
    dec.decide(str(repo), "2", "decline", {}, user_turns=2)
    code, msg = _commit(repo, home, payload, "feature", transcript)
    assert code == 0
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert [d["intent_covered"] for d in note["decisions"]] == [True, False]
    d = build(str(repo), "7d")
    it = d["intent"]
    assert it["sessions_with_intent"] == 1 and it["sessions"] == 1
    assert it["cards_with_intent"] == 1 and it["findings_on_those_cards"] == 2 and it["within_intent"] == 1
    assert it["within_intent_accepted"] == 1 and it["within_intent_declined"] == 0
    text = render(d)
    assert "Intent: 1 of 1 session stated one · 1 of 2 card findings fell within it (1 accepted, 0 declined)" in text
    assert 'intent "tighten the authz rules before the staging cut"' in text
    # no per-person figure anywhere in the intent line
    assert " by t" not in text.split("Intent:")[1].split("\n")[0]


def test_covers_ignores_shape_words_and_splits_paths_and_camel_case():
    assert im.covers(
        "fix the export",
        {"subject": "edit", "path": "src/api/OrdersExport.py", "finding": "edit src/api/OrdersExport.py"},
    )
    assert not im.covers(
        "edit the file in production",
        {"subject": "edit", "path": "values/production-in/app.yaml", "finding": "edit values/production-in/app.yaml"},
    )
    assert not im.covers("", {"finding": "anything"})
    assert im.terms("Add rate-limiting to /v1/orders/export") == {"rate", "limiting", "orders", "export"}


def test_intent_follows_the_session_into_a_second_repository(repo, home, payload, tmp_path, transcript):
    other = tmp_path / "other"
    other.mkdir()
    git(other, "init", "-q")
    git(other, "config", "user.email", "t@test")
    git(other, "config", "user.name", "t")
    (other / "b.txt").write_text("b\n")
    git(other, "add", "b.txt")
    git(other, "commit", "-qm", "init")
    _write_git_hook(str(other / ".gitvow" / "git-hooks"))
    git(other, "config", "core.hooksPath", ".gitvow/git-hooks")
    session_start(payload("SessionStart"), str(home))
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home))
    cmd = f"git -C {other} commit -am x"
    assert pre_tool_use(payload("PreToolUse", "Bash", {"command": cmd}, transcript), str(home))[0] == 0
    assert im.current(str(other))["text"].startswith("Let ops export")
    (other / "b.txt").write_text("changed\n")
    git(other, "commit", "-qam", "in the other repository")
    assert "Gitvow-Intent: Let ops export" in git(other, "log", "-1", "--format=%B")


def test_policy_switches_and_validation(repo, home, payload):
    with open(DEFAULT_POLICY_PATH) as fh:
        d = json.load(fh)
    (repo / ".gitvow").mkdir(exist_ok=True)
    d["intent"] = {"from_prompt": False, "ask_when_missing": False}
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    rc, ctx = session_start(payload("SessionStart"), str(home))
    assert rc == 0 and "intent" not in ctx
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home))
    assert im.current(str(repo)) is None
    d["intent"] = {"from_prompt": False, "ask_when_missing": True}
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    _, ctx = session_start(payload("SessionStart"), str(home))
    assert "ask once, in one line, before the first edit" in ctx and "first line" not in ctx
    d["intent"] = {"from_prompt": "yes"}
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    with pytest.raises(PolicyError, match=r"intent\.from_prompt must be true or false"):
        load_policy(str(repo), str(home))
    d["intent"] = []
    (repo / ".gitvow" / "policy.json").write_text(json.dumps(d))
    with pytest.raises(PolicyError, match="intent must be an object"):
        load_policy(str(repo), str(home))
    assert im.settings({})["from_prompt"] is True and im.settings(None)["ask_when_missing"] is True


def test_record_server_and_handoff_show_the_intent(repo, home, payload, transcript):
    _hooked(repo)
    session_start(payload("SessionStart"), str(home))
    assert "record_intent" in {t["name"] for t in tool_list()}
    r = call_tool("record_intent", {}, str(repo), str(home))
    assert r["data"]["current"] is None and "no intent recorded" in r["text"]
    user_prompt_submit({**payload("UserPromptSubmit"), "prompt": PROMPT}, str(home))
    _commit(repo, home, payload, "one", transcript)
    r = call_tool("record_intent", {"limit": 5}, str(repo), str(home))
    assert r["data"]["current"]["text"].startswith("Let ops export")
    assert len(r["data"]["recent"]) == 1 and r["data"]["recent"][0]["source"] == "prompt"
    assert 'current session intent: "Let ops export' in r["text"] and "intents on recent commits (1)" in r["text"]
    h = call_tool("record_handoff", {}, str(repo), str(home))
    assert "Intent: Let ops export orders as CSV from the admin page." in h["text"]
    rc = call_tool("record_recall", {"words": ["csv"]}, str(repo), str(home))
    assert "1 session mention csv" in rc["text"]
    from gitvow.report import build as report_build
    from gitvow.report import render_markdown

    rep = report_build(str(repo), "HEAD~1", "HEAD", None)
    assert rep["commits"][0]["intent"].startswith("Let ops export")
    assert "**Intent:** Let ops export" in render_markdown(rep)
