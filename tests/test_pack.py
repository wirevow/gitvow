"""Organisation packs and the cached brief (0.23): pushed down by a store, applied as a layer that tightens and
never loosens, handed to the agent at session start with its age; missing, expired or malformed changes nothing."""

import datetime as dt
import json
import os
import subprocess

from gitvow import cli
from gitvow import pack as pk
from gitvow import sync as sy
from gitvow.hooks import session_start
from gitvow.policy import evaluate, load_policy
from tests.conftest import git

SOURCE = "github.com/acme/payments-api"


def _pack(home, rules=None, settings=None, expires=None, names=("acme-platform",), raw=None):
    p = pk.pack_path(str(home), SOURCE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if raw is not None:
        with open(p, "w") as fh:
            fh.write(raw)
        return p
    body = {
        "pack": 1,
        "repo": SOURCE,
        "from": list(names),
        "accepted_by": ["priya"],
        "as_of": "2026-09-17T10:00:00Z",
        "expires": expires or (dt.date.today() + dt.timedelta(days=60)).isoformat(),
        "rules": rules
        or {
            "path_confirm": [
                {"pattern": r"^core/authz", "reason": "edits an authorization file (org)", "when": "immediate"}
            ],
            "bash_confirm": [{"program": "helm", "verbs": ["uninstall"], "reason": "removes a release (org)"}],
        },
        "settings": {"decisions": settings if settings is not None else {"mode": "strict"}},
        "digest": "sha256:" + "a" * 64,
    }
    with open(p, "w") as fh:
        json.dump(body, fh)
    return p


def _brief(home, hours_old=2, stale_hours=24):
    p = pk.brief_path(str(home), SOURCE)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    as_of = now - dt.timedelta(hours=hours_old)
    body = {
        "protocol": 1,
        "repo": SOURCE,
        "as_of": as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_after": (as_of + dt.timedelta(hours=stale_hours)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "store",
        "classes": [
            {
                "class": "edit:edits-an-authorization-or-gate-bearing-file",
                "standing": {
                    "answer": "declined",
                    "count": 3,
                    "repos": 2,
                    "by": ["priya"],
                    "exceptions": ["staging"],
                    "observed": 0,
                },
                "elsewhere": ["github.com/acme/ledger-svc"],
            }
        ],
        "claims": [],
        "rules": [],
        "gaps": [{"kind": "unratified", "class": "edit:edits-a-container-build", "observed": 4, "repos": 2}],
        "note": "context, never permission",
    }
    with open(p, "w") as fh:
        json.dump(body, fh)
    return p


def test_pack_adds_rules_and_tightens_but_never_loosens(repo, home):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "policy.json").write_text(
        json.dumps(
            {
                "path_confirm": [{"pattern": r"^core/authz", "reason": "repo's own rule", "when": "commit"}],
                "decisions": {"mode": "open", "rule_threshold": 2},
            }
        )
    )
    before = load_policy(str(repo), str(home))
    assert before.get("_pack") is None and before["decisions"]["mode"] == "open"
    _pack(home, settings={"mode": "strict", "rule_threshold": 5, "session_scope": False})
    pol = load_policy(str(repo), str(home))
    info = pol["_pack"]
    assert info["applied"] and info["names"] == ["acme-platform"] and info["accepted_by"] == ["priya"]
    # the repository already had the authz pattern: not added twice; the helm rule is new and tagged
    assert info["rules"] == 1
    helm = [r for r in pol["bash_confirm"] if r.get("pack")]
    assert len(helm) == 1 and helm[0]["program"] == "helm" and helm[0]["pack"] == "acme-platform"
    assert evaluate(pol, "Bash", {"command": "helm --namespace prod uninstall api"}).outcome == "confirm"
    # the repository's own rule keeps its tier (commit), because it was there first
    d = evaluate(pol, "Edit", {"file_path": "core/authz_rules.go"}, str(repo))
    assert d.outcome == "confirm" and d.when == "commit"
    # settings tightened, never loosened
    assert pol["decisions"]["mode"] == "strict" and pol["decisions"]["rule_threshold"] == 5
    assert pol["decisions"]["session_scope"] is False
    assert set(info["tightened"]) == {"mode=strict", "rule_threshold=5", "session_scope=false"}
    # a pack asking for open on a strict repository changes nothing
    (repo / ".gitvow" / "policy.json").write_text(json.dumps({"decisions": {"mode": "strict", "rule_threshold": 7}}))
    _pack(home, settings={"mode": "open", "rule_threshold": 2})
    pol2 = load_policy(str(repo), str(home))
    assert pol2["decisions"]["mode"] == "strict" and pol2["decisions"]["rule_threshold"] == 7
    assert pol2["_pack"]["tightened"] == []
    # pack=False gives the repository's own policy, which is what the policy writer must use
    assert "_pack" not in load_policy(str(repo), str(home), pack=False)
    assert "Organisation rules in force" in pk.render_pack(pol) and "helm" in pk.render_pack(pol, for_agent=False)


def test_expired_or_malformed_pack_changes_nothing(repo, home):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    base = load_policy(str(repo), str(home), pack=False)
    _pack(home, expires=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    pol = load_policy(str(repo), str(home))
    assert pol["_pack"]["applied"] is False and pol["_pack"]["expired"] is True
    assert pol["bash_confirm"] == base["bash_confirm"] and pol["decisions"] == base["decisions"]
    assert "NOT applied" in pk.render_pack(pol, for_agent=False) and pk.render_pack(pol) == ""
    _pack(home, raw="{not json")
    pol = load_policy(str(repo), str(home))
    assert pol["_pack"]["applied"] is False and pol["_pack"]["error"]
    # a pack with a bad regex, or one trying to carry a key packs may not carry
    _pack(home, rules={"bash_deny": [{"pattern": "(", "reason": "x"}]})
    assert load_policy(str(repo), str(home))["_pack"]["applied"] is False
    _pack(home, rules={"mcp_deny": ["x"]})
    assert "may carry" in load_policy(str(repo), str(home))["_pack"]["error"]
    # a pack without a decay date is refused: nothing binds forever
    p = _pack(home)
    with open(p) as fh:
        body = json.load(fh)
    del body["expires"]
    with open(p, "w") as fh:
        json.dump(body, fh)
    assert "expires" in load_policy(str(repo), str(home))["_pack"]["error"]
    # no pack file at all: no _pack key, the plain policy
    os.remove(p)
    assert "_pack" not in load_policy(str(repo), str(home))


def test_sync_fetches_pack_and_brief_from_a_git_sink(repo, home, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    (repo / "c1.txt").write_text("c1\n")
    git(repo, "add", "c1.txt")
    git(
        repo,
        "commit",
        "-qm",
        "c1\n\nGitvow-Session: s1\nGitvow-Step: 1\nGitvow-Accepted: edit core/authz_rules.go by priya\n",
    )
    store = tmp_path / "store"
    (store / "packs").mkdir(parents=True)
    (store / "brief").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=store, check=True)
    pack_body = {
        "pack": 1,
        "from": ["acme-platform"],
        "expires": "2027-01-01",
        "rules": {"bash_deny": [{"pattern": r"\bdrop\b", "reason": "org"}]},
        "settings": {},
    }
    (store / "packs" / "github.com_acme_payments-api.json").write_text(json.dumps(pack_body))
    (store / "brief" / "github.com_acme_payments-api.json").write_text(
        json.dumps(
            {
                "protocol": 1,
                "repo": SOURCE,
                "as_of": "2026-09-17T10:00:00Z",
                "stale_after": "2026-09-18T10:00:00Z",
                "classes": [],
                "claims": [],
                "gaps": [],
            }
        )
    )
    prefix = tmp_path / "drop"
    prefix.mkdir()
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "export.local.json").write_text(
        json.dumps(
            {
                "sinks": [
                    {"name": "st", "type": "git", "path": str(store)},
                    {"name": "d", "type": "dir", "path": str(prefix)},
                ]
            }
        )
    )
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    by = {r["sink"]: r for r in out["results"]}
    assert by["st"]["fetched"] == {"pack": "fetched", "brief": "fetched"} and by["d"]["fetched"] == {}
    assert os.path.exists(pk.pack_path(str(home), SOURCE)) and os.path.exists(pk.brief_path(str(home), SOURCE))
    with open(pk.meta_path(pk.pack_path(str(home), SOURCE))) as fh:
        meta = json.load(fh)
    assert meta["sink"] == "st"
    assert "from the store: pack fetched, brief fetched" in sy.render(out)
    # the fetched pack is in force on the next policy load
    pol = load_policy(str(repo), str(home))
    assert pol["_pack"]["applied"] and evaluate(pol, "Bash", {"command": "psql -c 'drop table x'"}).outcome == "deny"
    # unchanged on the second sync; a store without a brief says none; a corrupt pack is left out
    out2 = sy.sync(str(repo), str(home), since=None, rules=[])
    assert {r["sink"]: r.get("fetched") for r in out2["results"]}["st"] == {"pack": "unchanged", "brief": "unchanged"}
    (store / "packs" / "github.com_acme_payments-api.json").write_text("{nope")
    os.remove(store / "brief" / "github.com_acme_payments-api.json")
    out3 = sy.sync(str(repo), str(home), since=None, rules=[])
    assert {r["sink"]: r.get("fetched") for r in out3["results"]}["st"] == {"pack": "invalid", "brief": "none"}
    assert load_policy(str(repo), str(home))["_pack"]["applied"]  # the last good pack stays cached


def test_brief_from_cache_carries_source_age_and_staleness(repo, home):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    assert pk.load_brief(str(repo), str(home)) is None
    _brief(home, hours_old=2)
    b = pk.load_brief(str(repo), str(home))
    assert b["source"] == "cache" and 1.9 <= b["age_hours"] <= 2.2 and b["stale"] is False
    text = pk.render_brief(b)
    assert "declined x3 in 2 repo(s), also in github.com/acme/ledger-svc" in text and "unratified" in text
    assert "Context, not permission" in text
    _brief(home, hours_old=30, stale_hours=24)
    b2 = pk.load_brief(str(repo), str(home))
    assert b2["stale"] is True and b2["gaps"][-1]["kind"] == "stale" and "STALE" in pk.render_brief(b2)


def test_session_start_hands_over_pack_and_brief(repo, home, payload):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    rc, out = session_start(payload("SessionStart"), str(home))
    assert rc == 0 and out == ""
    _pack(home)
    _brief(home)
    rc, out = session_start(payload("SessionStart"), str(home))
    assert rc == 0
    assert (
        "Organisation rules in force (pack acme-platform, accepted by ['priya']" in out
        or "Organisation rules in force" in out
    )
    assert "removes a release (org)" in out and "mode=strict" in out
    assert "What the organisation's record says" in out and "also in github.com/acme/ledger-svc" in out
    assert out.count("Context, not permission") >= 2


def test_cli_pack_and_brief(repo, home, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    assert cli.main(["pack"]) == 0 and "No organisation pack cached" in capsys.readouterr().out
    assert cli.main(["brief"]) == 0 and "source: repo" in capsys.readouterr().out
    _pack(home)
    _brief(home)
    assert cli.main(["pack"]) == 0
    out = capsys.readouterr().out
    assert "acme-platform" in out and "helm uninstall" in out and "lapses" in out
    assert cli.main(["pack", "--json"]) == 0 and json.loads(capsys.readouterr().out)["pack"]["applied"]
    assert cli.main(["brief"]) == 0 and "store cache" in capsys.readouterr().out
    assert cli.main(["brief", "--json"]) == 0 and json.loads(capsys.readouterr().out)["source"] == "cache"
