"""0.29.2: in Claude Code, a confirm is the agent's own question to the person when the permission mode shows one."""

import io
import json
import sys

from gitvow import cli
from gitvow.adapters import native_question, respond
from gitvow.decisions import CARD_HEADER, OPEN_CARD_HEADER
from gitvow.hooks import post_tool_use, session_start
from tests.conftest import git

CONFIRM = (
    "CONFIRMATION REQUIRED (pushing to a protected branch). Ask the user before doing this. If they agree, "
    "record it and run the command again:\n  gitvow decide 1 accept --scope session"
)


def test_prompting_modes_ask_and_other_modes_block():
    for mode in ("default", "acceptEdits", "plan"):
        code, out, err = respond("claude", 2, CONFIRM, mode=mode)
        assert code == 0 and err == ""
        h = json.loads(out)["hookSpecificOutput"]
        assert h["permissionDecision"] == "ask" and h["hookEventName"] == "PreToolUse"
        assert h["permissionDecisionReason"].startswith("gitvow: pushing to a protected branch.")
    for mode in ("bypassPermissions", "dontAsk", "auto", None, "weird"):
        assert respond("claude", 2, CONFIRM, mode=mode) == (2, "", CONFIRM)


def test_denials_and_the_strict_card_are_never_a_question():
    assert respond("claude", 2, "BLOCKED by policy (force push).", mode="default")[0] == 2
    card = CARD_HEADER + "\n1. edit values/production-in/app.yaml"
    assert respond("claude", 2, card, mode="default")[0] == 2
    open_card = OPEN_CARD_HEADER + ": 1 finding nobody has answered\n1. edit values/production-in/app.yaml"
    code, out, _ = respond("claude", 2, open_card, mode="default")
    assert code == 0 and "Gitvow-Open" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]


def test_native_question_wording():
    q = native_question(CONFIRM)
    assert "gitvow decide" not in q and "Allow records your approval" in q


def test_hook_cli_uses_the_payloads_permission_mode(repo, home, payload, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(home))
    git(repo, "remote", "add", "origin", "git@example.com:acme/ledger.git")
    session_start(payload("SessionStart"), str(home))
    p = {**payload("PreToolUse", "Bash", {"command": "git push origin main"}), "permission_mode": "default"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(p)))
    rc = cli.main(["hook", "PreToolUse"])
    out = capsys.readouterr().out
    assert rc == 0 and json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    # the person clicked Allow: the command ran, PostToolUse records the approval for the session
    q = payload("PostToolUse", "Bash", {"command": "git push origin main"})
    post_tool_use(q, str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    dec = [f["decision"] for f in st["findings"] if f.get("decision")]
    assert dec and dec[0]["answer"] == "accepted" and dec[0]["scope"] == "session"
    assert "approved at the agent's prompt" in (dec[0].get("note") or dec[0].get("reason") or "")
    # the same call under bypass is a block, not a question
    p["permission_mode"] = "bypassPermissions"
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(p)))
    rc = cli.main(["hook", "PreToolUse"])
    assert rc in (0, 2)  # 0 when the session answer now allows it; the point is that no `ask` JSON is emitted
    assert "permissionDecision" not in capsys.readouterr().out
