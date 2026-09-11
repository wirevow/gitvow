import gitvow.decisions as dec
from gitvow.hooks import pre_tool_use, session_start


def _raise_finding(payload, home, repo, transcript):
    session_start(payload("SessionStart"), str(home))
    edit = payload("PreToolUse", "Edit", {"file_path": str(repo / "core/authz_rules.go")}, transcript)
    assert pre_tool_use(edit, str(home)) == (0, "")
    assert dec.undecided(str(repo)), "no finding was raised; the fixture policy changed"


def test_card_fires_for_every_global_option_form(repo, home, payload, transcript):
    """The end-to-end version of the regression: not just that the pattern matches, but that the card appears.

    `gitvow rules`/the trailer work is irrelevant here — what the published bypass page claims is that an
    agent running `git -C dir commit` is now stopped, and that claim has to be true of the whole path.
    """
    for cmd in (
        "git commit -m x",
        "git -c core.hooksPath=/dev/null commit -m x",
        "git -C . commit -m x",
        "git -c user.name=a -c user.email=b commit -m x",
        "cd sub && git -c a=b commit -am x",
    ):
        _raise_finding(payload, home, repo, transcript)
        code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": cmd}, transcript), str(home))
        assert code == 2, f"no card for {cmd!r}"
        assert "DECISIONS REQUIRED" in msg, f"card text missing for {cmd!r}"


def test_plumbing_that_merely_starts_with_commit_does_not_raise_the_card(repo, home, payload, transcript):
    _raise_finding(payload, home, repo, transcript)
    for cmd in ("git commit-tree $t", "git commit-graph write"):
        code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": cmd}, transcript), str(home))
        assert code == 0 and not msg, f"unexpected card for {cmd!r}"
