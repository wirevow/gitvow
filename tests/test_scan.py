import json
import subprocess

from gitvow import cli
from gitvow.scan import _since_date, build, render


def _commit(repo, path, body, content="x\n"):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", body], cwd=repo, check=True, capture_output=True)


def test_since_shorthand():
    assert _since_date("2026-01-01") == "2026-01-01"
    assert len(_since_date("90d")) == 10 and _since_date("90d") < _since_date("1d")
    assert _since_date("6m") < _since_date("30d") and _since_date("1y") < _since_date("6m")


def test_a_repository_with_nothing_recorded(repo, home):
    _commit(repo, "core/auth_rules.go", "expose export\n\nCo-authored-by: Claude <noreply@anthropic.com>")
    _commit(repo, "src/util.py", "tidy\n\nCo-authored-by: Cursor <cursoragent@cursor.com>")
    _commit(repo, ".github/workflows/ci.yml", "ci: add a job\n\nGenerated with [Claude Code]")
    _commit(repo, "docs/readme.md", "a human wrote this one")
    d = build(str(repo))
    assert d["commits"] == 5 and d["agent_commits"] == 3  # the fixture's own initial commit is the fifth
    assert d["agent_share"] == 0.6
    assert set(d["agents"]) == {"Claude", "Cursor"}
    # only the two that touched a path the policy calls consequential
    assert len(d["consequential"]) == 2
    assert {f for c in d["consequential"] for f in c["files"]} == {"core/auth_rules.go", ".github/workflows/ci.yml"}
    assert d["recorded"] == 0 and d["unrecorded"] == 2 and d["open"] == 0
    text = render(d)
    assert "agent-assisted" in text and "touched a gated file" in text
    assert "Nobody can tell you who agreed to them." in text
    assert "the default policy" in text  # no policy installed in this fixture
    assert "a floor, not a total" in text


def test_a_repository_where_it_is_recorded(repo, home):
    _commit(
        repo,
        "core/auth_rules.go",
        "expose export\n\nCo-authored-by: Claude <noreply@anthropic.com>\n"
        "Gitvow-Session: s1\nGitvow-Accepted: edit core/auth_rules.go by nikhil scope=staging: reviewed",
    )
    _commit(
        repo,
        "core/authz.py",
        "second one\n\nCo-authored-by: Claude <noreply@anthropic.com>\nGitvow-Open: edit core/authz.py",
    )
    d = build(str(repo))
    assert d["agent_commits"] == 2 and len(d["consequential"]) == 2
    assert d["recorded"] == 1 and d["unrecorded"] == 1 and d["open"] == 1
    assert d["consequential"][1]["by"] == ["nikhil"] and d["consequential"][1]["recorded"] is True
    text = render(d)
    assert "1 of 2 record who agreed. The other 1 does not." in text
    assert "1 carries an open finding nobody has answered." in text


def test_everything_recorded_says_so(repo, home):
    _commit(
        repo,
        "core/auth_rules.go",
        "one\n\nCo-authored-by: Claude <x@y>\nGitvow-Accepted: edit core/auth_rules.go by nikhil",
    )
    text = render(build(str(repo)))
    assert "All 1 record who agreed, with evidence." in text


def test_no_agent_signatures_is_not_a_failure(repo, home):
    _commit(repo, "src/util.py", "a person wrote this")
    d = build(str(repo))
    assert d["agent_commits"] == 0 and d["agent_share"] == 0.0
    text = render(d)
    assert "no commits carry an agent's signature" in text
    assert "whether the agent signs or not" in text
    assert "pipx install gitvow" in text


def test_an_empty_window_suggests_a_longer_one(repo, home):
    d = build(str(repo), since="2099-01-01")
    assert d["commits"] == 0 and d["agent_share"] is None
    assert "gitvow scan --since 1y" in render(d)


def test_cli_scan_text_and_json(repo, home, monkeypatch, capsys):
    _commit(repo, "core/auth_rules.go", "x\n\nCo-authored-by: Claude <x@y>")
    monkeypatch.chdir(repo)
    assert cli.main(["scan"]) == 0
    assert "agent-assisted" in capsys.readouterr().out
    assert cli.main(["scan", "--json", "--since", "1y"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["repo"] == "repo" and d["agent_commits"] == 1 and d["window"] == "1y"
    assert cli.main(["scan", "/nonexistent-path-for-scan"]) == 1
    assert "not a git repository" in capsys.readouterr().err
