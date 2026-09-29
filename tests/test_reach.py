"""0.32: reach. Who owns the path a finding is about, from CODEOWNERS at HEAD; unowned surface counted."""

import json

from gitvow import cli
from gitvow import decisions as dec
from gitvow import reach as rc
from gitvow.digest import build, render
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.serve import call_tool, tool_list
from tests.conftest import git

OWNERS = """# comment
*            @acme/everyone
*.go         @acme/backend
/core/       @acme/platform @priya
docs/*.md    @acme/docs
values/production-in/  @acme/sre
scratch/
[Section]
apps/**/deploy.yaml   ops@acme.example
"""


def _owned(repo, text=OWNERS, where=".github/CODEOWNERS"):
    p = repo / where
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", where)
    git(repo, "commit", "-qm", "owners")
    return git(repo, "rev-parse", "HEAD")[:12]


def test_pattern_semantics():
    def m(pattern, path):
        return bool(rc.compile_pattern(pattern).search(path))

    assert m("*.go", "a.go") and m("*.go", "x/y/a.go") and not m("*.go", "a.gox")
    assert m("*", "anything/at/all")
    assert m("/core/", "core/authz.go") and not m("/core/", "x/core/authz.go")
    assert m("docs/*.md", "docs/a.md") and not m("docs/*.md", "docs/sub/a.md") and not m("docs/*.md", "x/docs/a.md")
    assert m("apps/**/deploy.yaml", "apps/a/b/deploy.yaml") and m("apps/**/deploy.yaml", "apps/deploy.yaml")
    assert m("values/production-in/", "values/production-in/app.yaml") and not m(
        "values/production-in/", "values/production-in"
    )
    assert m("README", "README") and m("README", "x/README") and m("README", "README/inner")
    assert m("a?c", "abc") and not m("a?c", "a/c")


def test_parse_last_match_wins_sections_and_explicit_unowned():
    rules = rc.parse(OWNERS)
    assert [r["pattern"] for r in rules][:3] == ["*", "*.go", "/core/"]
    assert rc.match(rules, "core/authz.go")["owners"] == ["@acme/platform", "@priya"]  # last match, not *.go
    assert rc.match(rules, "lib/a.go")["owners"] == ["@acme/backend"]
    assert rc.match(rules, "docs/x.md")["owners"] == ["@acme/docs"]
    assert rc.match(rules, "apps/svc/deploy.yaml")["owners"] == ["ops@acme.example"]  # after a GitLab section header
    assert rc.match(rules, "scratch/tmp.txt")["owners"] == []  # a pattern with no owner: explicitly unowned
    assert rc.match(rules, "anything.txt")["owners"] == ["@acme/everyone"]
    assert rc._split("path\\ with\\ space @a") == ["path with space", "@a"]


def test_load_from_head_and_resolve_with_fallbacks(repo, home):
    assert rc.load_owners(str(repo)) is None
    o = rc.resolve(str(repo), "core/x.go", {"decisions": {"authorities": ["nikhil"]}})
    assert o == {"owners": ["nikhil"], "source": "policy.authorities", "pattern": None, "unowned": False}
    o = rc.resolve(str(repo), "core/x.go", {})
    assert o["unowned"] is True and o["source"] is None
    sha = _owned(repo)
    loaded = rc.load_owners(str(repo))
    assert loaded["path"] == ".github/CODEOWNERS" and loaded["commit"] == sha and len(loaded["rules"]) == 7
    o = rc.resolve(str(repo), "core/authz_rules.go", {})
    assert (
        o["owners"] == ["@acme/platform", "@priya"]
        and o["source"] == f".github/CODEOWNERS@{sha}"
        and o["pattern"] == "/core/"
    )
    # explicitly unowned in the file: the file is named as the source, the policy is not consulted
    o = rc.resolve(str(repo), "scratch/t.txt", {"decisions": {"authorities": ["nikhil"]}})
    assert o["unowned"] is True and o["owners"] == [] and o["source"].startswith(".github/CODEOWNERS@")
    # the working tree does not count: only what HEAD says
    (repo / ".github" / "CODEOWNERS").write_text("* @nobody\n")
    assert rc.resolve(str(repo), "core/a.go", {})["owners"] == ["@acme/platform", "@priya"]
    assert rc._path_of("edit core/authz_rules.go") == "core/authz_rules.go"
    assert rc._path_of("route /v1/orders/export in src/api/orders.py") == "src/api/orders.py"
    assert rc._path_of("remove route /v1/old from src/api/legacy.py") == "src/api/legacy.py"
    assert rc._path_of("run git (pushing to a protected branch) @ remote=x branch=main") is None
    assert rc._path_of("run kubectl (cluster mutation in production) @ context=prod") is None


def test_findings_carry_the_owner_the_card_shows_it_and_refer_defaults_to_it(repo, home, payload, transcript):
    sha = _owned(repo, "core/  @acme/platform\n", where="CODEOWNERS")
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    session_start(payload("SessionStart"), str(home))
    pre_tool_use(payload("PreToolUse", "Edit", {"file_path": "core/authz_rules.go"}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Write", {"file_path": ".github/workflows/ci.yml"}, transcript), str(home))
    pre_tool_use(payload("PreToolUse", "Bash", {"command": "git push origin main"}, transcript), str(home))  # asks
    fs = dec.open_findings(str(repo))
    assert fs[0]["owner"]["owners"] == ["@acme/platform"] and fs[0]["owner"]["source"] == f"CODEOWNERS@{sha}"
    assert fs[1]["owner"]["unowned"] is True and fs[1]["owner"]["owners"] == []
    assert fs[2]["owner"] is None  # a command names no path
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 2
    assert f"   owner: @acme/platform (CODEOWNERS@{sha}, core/)" in msg
    assert "   not their call? gitvow decide 1 refer --to @acme/platform" in msg
    assert "   owner: none (unowned surface; CODEOWNERS@" in msg and "has no rule for it)" in msg
    # a referral with nobody named goes to the owner; a name given always wins
    done = dec.decide(str(repo), "1", "refer", {})
    assert done[0]["decision"]["to"] == "@acme/platform"
    done = dec.decide(str(repo), "2", "refer", {}, to="security")
    assert done[0]["decision"]["to"] == "security"
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    assert code == 0
    (repo / "a.txt").write_text("x\n")
    git(repo, "commit", "-qam", "feature")
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Referred: edit core/authz_rules.go by t to=@acme/platform" in body
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m x"}, transcript), str(home))
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 9
    assert note["decisions"][0]["owner"]["owners"] == ["@acme/platform"] and note["decisions"][0]["unowned"] is False
    assert note["decisions"][1]["unowned"] is True
    # report shows the owner and flags the unowned path
    from gitvow.report import build as report_build
    from gitvow.report import render_markdown

    rep = render_markdown(report_build(str(repo), "HEAD~1", "HEAD", None))
    assert "· owner @acme/platform" not in rep  # a referral line names whom it went to instead
    assert "to @acme/platform" in rep and "**unowned path**" in rep


def test_summary_digest_cli_and_server(repo, home, monkeypatch, capsys):
    sha = _owned(repo, "core/  @acme/platform\n", where="CODEOWNERS")
    for name, trailer in (
        ("core/a.go", "Gitvow-Accepted: edit core/a.go by t"),
        ("core/b.go", "Gitvow-Declined: edit core/b.go by t"),
        ("infra/x.tf", "Gitvow-Accepted: edit infra/x.tf by t"),
        ("infra/x.tf", "Gitvow-Open: edit infra/x.tf"),
        ("z", "Gitvow-Accepted: run kubectl (cluster mutation in production) @ context=prod by t"),
    ):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(trailer + "\n")
        git(repo, "add", name)
        git(repo, "commit", "-qm", f"{name}\n\nGitvow-Session: s\n{trailer}")
    s = rc.summary(str(repo), {})
    assert s["owners_file"] == f"CODEOWNERS@{sha}" and s["rules"] == 1
    assert s["decided_paths"] == 4 and s["resolved"] == 2 and s["unowned_paths"] == 1
    assert s["unowned"] == [("infra/x.tf", 2)] and s["owners"] == [("@acme/platform", 2)]
    assert (
        rc.summary_line(s)
        == f"Reach: 2 of 4 decided paths have an owner · 1 unowned path carry decisions · owners from CODEOWNERS@{sha}"
    )
    d = build(str(repo), "7d")
    assert d["reach"]["unowned_paths"] == 1 and "Reach: 2 of 4 decided paths" in render(d)
    monkeypatch.chdir(repo)
    assert cli.main(["reach", "core/a.go"]) == 0
    assert capsys.readouterr().out == f"core/a.go: @acme/platform  (CODEOWNERS@{sha}, rule core/)\n"
    assert cli.main(["reach", "infra/x.tf"]) == 1
    assert "infra/x.tf: unowned" in capsys.readouterr().out
    assert cli.main(["reach"]) == 0
    out = capsys.readouterr().out
    assert (
        "ownership file: CODEOWNERS@" in out and "unowned paths carrying decisions:\n  infra/x.tf  (2 decisions)" in out
    )
    assert cli.main(["reach", "--json"]) == 0 and json.loads(capsys.readouterr().out)["resolved"] == 2
    assert "record_reach" in {t["name"] for t in tool_list()}
    r = call_tool("record_reach", {"path": "core/a.go"}, str(repo), str(home))
    assert r["data"]["owners"] == ["@acme/platform"]
    r = call_tool("record_reach", {}, str(repo), str(home))
    assert r["data"]["unowned_paths"] == 1 and "Reach: 2 of 4" in r["text"]


def test_no_ownership_file_renders_and_counts_honestly(repo, home, monkeypatch, capsys):
    (repo / "a.txt").write_text("x\n")
    git(repo, "commit", "-qam", "d\n\nGitvow-Session: s\nGitvow-Accepted: edit a.txt by t")
    s = rc.summary(str(repo), {})
    assert s["owners_file"] is None and s["resolved"] == 0 and s["unowned_paths"] == 1
    assert rc.summary_line(s).endswith("· no ownership file")
    monkeypatch.chdir(repo)
    assert cli.main(["reach"]) == 0 and "no ownership file at HEAD" in capsys.readouterr().out
    assert rc.summary_line({"decided_paths": 0}) == ""
