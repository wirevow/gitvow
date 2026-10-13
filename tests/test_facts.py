"""Runtime facts at the gate (0.39): the brief's facts[] rendered with age; an edit to a file carrying a silent
route raises an observe finding, an erroring route a commit-tier one; both carry the observation's ref into the
state, the note, and the export; no network is touched; age and coverage gate what is acted on."""

import json
import os
import time

from gitvow import cli
from gitvow import export as ex
from gitvow import facts as fx
from gitvow import pack as pk
from gitvow.hooks import post_tool_use, pre_tool_use, session_start
from gitvow.install import _write_git_hook
from gitvow.policy import Decision
from tests.conftest import git

SRC = "github.com/acme/orchestrator"


def _fact(subject, path, answer, facts, ref, coverage="measured", age=26.0):
    return {
        "provider": "runtime",
        "ref": ref,
        "subject": subject,
        "subject_type": "route",
        "path": path,
        "line": 120,
        "service": "orchestrator",
        "env": "production-in",
        "answer": answer,
        "facts": facts,
        "window": "7d",
        "coverage": coverage,
        "as_of": "2026-10-12T06:00:00Z",
        "age_hours": age,
        "reopen": ["deploy of orchestrator in production-in", "telemetry coverage change"],
    }


def _brief(home, facts, hours_old=1):
    now = time.gmtime(time.time() - hours_old * 3600)
    later = time.gmtime(time.time() + 23 * 3600)
    b = {
        "protocol": pk.BRIEF_PROTOCOL,
        "repo": SRC,
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", now),
        "stale_after": time.strftime("%Y-%m-%dT%H:%M:%SZ", later),
        "source": "store",
        "classes": [],
        "claims": [],
        "rules": [],
        "facts": facts,
        "routing": [],
        "gaps": [],
        "note": "context, never permission",
    }
    p = pk.brief_path(str(home), SRC)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(b, fh)
    return b


FACTS = [
    _fact(
        "GET /v1/orders/{id}",
        "api/orders.py",
        "1700 requests in 7d, p99 79 ms, 5xx 0.0%, no SLO defined",
        {"requests": 1700, "p99_ms": 79.0, "error_rate_5xx": 0.0, "slo": None},
        "obs_" + "a" * 32,
    ),
    _fact(
        "POST /v1/orders",
        "api/orders.py",
        "0 requests in 7d, no SLO defined",
        {"requests": 0, "p99_ms": None, "error_rate_5xx": None, "slo": None},
        "obs_" + "b" * 32,
    ),
    _fact(
        "GET /v1/reports",
        "api/reports.py",
        "200 requests in 7d, p99 790 ms, 5xx 3.0%, no SLO defined",
        {"requests": 200, "p99_ms": 790.0, "error_rate_5xx": 0.03, "slo": None},
        "obs_" + "c" * 32,
    ),
    _fact(
        "GET /v1/health",
        "api/health.py",
        "no telemetry for this service",
        {"requests": None},
        "obs_" + "d" * 32,
        coverage="no_telemetry",
    ),
    _fact(
        "GET /v1/old",
        "api/old.py",
        "0 requests in 7d",
        {"requests": 0, "error_rate_5xx": None},
        "obs_" + "e" * 32,
        age=400.0,
    ),
]


def test_brief_renders_facts_with_age_and_cli_shows_them(repo, home, monkeypatch, capsys):
    git(repo, "remote", "add", "origin", "git@github.com:acme/orchestrator.git")
    _brief(home, FACTS)
    b = pk.load_brief(str(repo), str(home))
    text = pk.render_brief(b)
    assert (
        "runtime facts on record: 5" in text
        and "GET /v1/orders/{id} in api/orders.py: 1700 requests" in text
        and "(26 h old) [obs_aaaa" in text
    )
    assert pk.context(str(repo), str(home), {})  # facts alone are enough for session-start context
    monkeypatch.chdir(repo)
    assert cli.main(["brief"]) == 0
    assert "runtime facts on record" in capsys.readouterr().out


def test_findings_from_facts_silent_observe_erroring_commit_with_refs(repo, home):
    git(repo, "remote", "add", "origin", "git@github.com:acme/orchestrator.git")
    _brief(home, FACTS)
    f = fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "orders.py")}, {})
    assert len(f) == 1 and f[0]["tier"] == "observe" and f[0]["observations"] == ["obs_" + "b" * 32]
    assert f[0]["finding"] == "edit api/orders.py (route POST /v1/orders silent over 7d)"
    assert any("observation obs_bbbb" in e for e in f[0]["evidence"]) and any(
        "reopens on" in e for e in f[0]["evidence"]
    )
    f = fx.findings_from_facts(str(repo), str(home), "Write", {"file_path": str(repo / "api" / "reports.py")}, {})
    assert (
        len(f) == 1
        and f[0]["tier"] == "commit"
        and "erroring, 5xx 3.0%" in f[0]["finding"]
        and f[0]["observations"] == ["obs_" + "c" * 32]
    )
    # no telemetry and a stale fact raise nothing; a non-edit tool raises nothing; settings change tiers and thresholds
    assert (
        fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "health.py")}, {}) == []
    )
    assert fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "old.py")}, {}) == []
    assert fx.findings_from_facts(str(repo), str(home), "Bash", {"command": "ls"}, {}) == []
    f = fx.findings_from_facts(
        str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "old.py")}, {"runtime": {"max_age_hours": 0}}
    )
    assert len(f) == 1  # 0 turns the age limit off
    f = fx.findings_from_facts(
        str(repo),
        str(home),
        "Edit",
        {"file_path": str(repo / "api" / "reports.py")},
        {"runtime": {"error_rate_confirm": 0.05}},
    )
    assert f == []
    f = fx.findings_from_facts(
        str(repo),
        str(home),
        "Edit",
        {"file_path": str(repo / "api" / "orders.py")},
        {"runtime": {"silent_route": "off"}},
    )
    assert f == []
    # merge: allow + observe finding → observed confirm; + commit finding → deferred; deny untouched; immediate wins
    d = fx.merge(
        Decision("allow"),
        fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "orders.py")}, {}),
        "x",
    )
    assert d.outcome == "confirm" and d.when == "observe" and d.observed
    d = fx.merge(
        Decision("allow"),
        fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "reports.py")}, {}),
        "x",
    )
    assert d.deferred and d.findings[0]["observations"] == ["obs_" + "c" * 32]
    assert fx.merge(Decision("deny", "no"), [{"tier": "commit", "reason": "r", "finding": "f"}], "x").outcome == "deny"
    d = fx.merge(
        Decision("allow"),
        [{"tier": "immediate", "reason": "r", "finding": "f", "kind": "edit", "subject": "s", "path": "p"}],
        "x",
    )
    assert d.blocks and d.when == "immediate"


def test_many_routes_in_one_file_are_one_finding_per_kind(repo, home):
    """0.39.1: a controller carrying twenty sleeping routes is one question with every ref on it, not twenty."""
    git(repo, "remote", "add", "origin", "git@github.com:acme/orchestrator.git")
    many = [
        _fact(f"GET /v1/r{i}", "api/many.py", "0 requests in 7d", {"requests": 0}, "obs_" + f"{i:032x}")
        for i in range(7)
    ]
    many.append(
        _fact(
            "GET /v1/bad",
            "api/many.py",
            "10 requests in 7d, 5xx 20.0%",
            {"requests": 10, "error_rate_5xx": 0.2},
            "obs_" + "f" * 32,
        )
    )
    many.append(
        _fact(
            "GET /v1/bad2",
            "api/many.py",
            "10 requests in 7d, 5xx 2.0%",
            {"requests": 10, "error_rate_5xx": 0.02},
            "obs_" + "9" * 32,
        )
    )
    _brief(home, FACTS + many)
    f = fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": str(repo / "api" / "many.py")}, {})
    assert [x["finding"] for x in f] == [
        "edit api/many.py (7 routes silent over 7d)",
        "edit api/many.py (2 routes erroring, worst GET /v1/bad 5xx 20.0%)",
    ]
    assert len(f[0]["observations"]) == 7 and f[1]["observations"] == ["obs_" + "f" * 32, "obs_" + "9" * 32]
    assert (
        any("and 3 more route(s)" in e for e in f[0]["evidence"])
        and f[0]["tier"] == "observe"
        and f[1]["tier"] == "commit"
    )
    assert any(e.startswith("observation obs_") and "and 3 more" in e for e in f[0]["evidence"])


def test_through_the_hooks_into_state_note_and_export(repo, home, payload):
    git(repo, "remote", "add", "origin", "git@github.com:acme/orchestrator.git")
    _brief(home, FACTS)
    session_start(payload("SessionStart"), str(home))
    (repo / "api").mkdir()
    (repo / "api" / "reports.py").write_text("v1\n")
    (repo / "api" / "orders.py").write_text("v1\n")
    code, _ = pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(repo / "api" / "reports.py")}), str(home))
    assert code == 0  # commit-tier: recorded, the edit runs
    code, _ = pre_tool_use(payload("PreToolUse", "Edit", {"file_path": str(repo / "api" / "orders.py")}), str(home))
    assert code == 0
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    by = {f["finding"]: f for f in st["findings"]}
    err = by["edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)"]
    assert err["observations"] == ["obs_" + "c" * 32] and not err.get("observe")
    silent = by["edit api/orders.py (route POST /v1/orders silent over 7d)"]
    assert silent["observations"] == ["obs_" + "b" * 32] and silent.get("observe")
    log = (repo / ".git" / "gitvow-hooks.log").read_text()
    assert '"kind": "finding"' in log and '"kind": "observed"' in log
    # commit: the card is shown once (open mode), then the commit goes through with the note carrying the refs
    _write_git_hook(str(repo / ".gitvow" / "git-hooks"))
    git(repo, "config", "core.hooksPath", ".gitvow/git-hooks")
    git(repo, "add", "api")
    code, msg = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m reports"}), str(home))
    assert code == 2 and "erroring" in msg and "observation obs_cccc" in msg
    code, _ = pre_tool_use(payload("PreToolUse", "Bash", {"command": "git commit -m reports"}), str(home))
    assert code == 0
    git(repo, "commit", "-qm", "reports")
    post_tool_use(payload("PostToolUse", "Bash", {"command": "git commit -m reports"}), str(home))
    body = git(repo, "log", "-1", "--format=%B")
    assert "Gitvow-Open: edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)" in body
    note = json.loads(git(repo, "notes", "--ref=gitvow/sess-1", "show", "HEAD").split("\n", 1)[1])
    assert note["schema"] == 10
    dec = {d["finding"]: d for d in note["decisions"]}
    assert dec["edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)"]["observations"] == ["obs_" + "c" * 32]
    assert dec["edit api/orders.py (route POST /v1/orders silent over 7d)"]["answer"] == "observed"
    b = ex.build(str(repo), since=None, rules=[])
    rows = {r["finding"]: r for r in b["parts"]["decisions"]}
    assert rows["edit api/reports.py (route GET /v1/reports erroring, 5xx 3.0%)"]["observations"] == ["obs_" + "c" * 32]
    obs = {r["finding"]: r for r in b["parts"]["observed"]}
    assert "edit api/orders.py (route POST /v1/orders silent over 7d)" in obs


def test_drift_on_a_manifest_is_one_finding_with_the_ref(repo, home):
    """0.40: the blueprint's observation on a manifest raises a commit-tier finding naming the drift."""
    git(repo, "remote", "add", "origin", "git@github.com:acme/orchestrator.git")
    svc = _fact(
        "orchestrator",
        "values/production-in/orchestrator/values.yaml",
        "drifting: image tag declared c333333-12, running b222222-11",
        {
            "declared": {"image_tag": "c333333-12"},
            "observed": {"image_tags": ["b222222-11"], "ready": 1, "desired": 2},
            "drift": ["image tag declared c333333-12, running b222222-11", "ready 1 of 2"],
        },
        "obs_" + "d" * 32,
    )
    svc["subject_type"] = "service"
    ok = _fact(
        "ledger",
        "values/production-in/ledger/values.yaml",
        "running as declared, 2 of 2 ready",
        {"declared": {}, "observed": {"ready": 2, "desired": 2}, "drift": []},
        "obs_" + "e" * 32,
    )
    ok["subject_type"] = "service"
    _brief(home, [svc, ok])
    path = str(repo / "values" / "production-in" / "orchestrator" / "values.yaml")
    f = fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": path}, {})
    assert len(f) == 1 and f[0]["tier"] == "commit" and f[0]["observations"] == ["obs_" + "d" * 32]
    assert (
        f[0]["finding"]
        == "edit values/production-in/orchestrator/values.yaml (service orchestrator drifting: image tag declared c333333-12, running b222222-11 and 1 more)"
    )
    assert f[0]["evidence"][0].startswith("image tag declared") and any(
        e.startswith("observation obs_dddd") for e in f[0]["evidence"]
    )
    assert (
        fx.findings_from_facts(
            str(repo),
            str(home),
            "Edit",
            {"file_path": str(repo / "values" / "production-in" / "ledger" / "values.yaml")},
            {},
        )
        == []
    )
    assert (
        fx.findings_from_facts(str(repo), str(home), "Edit", {"file_path": path}, {"runtime": {"drift": "off"}}) == []
    )
