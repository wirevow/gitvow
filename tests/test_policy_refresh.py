"""0.28.3: an install refreshes an untouched copy of an older default policy, and leaves an edited one alone."""

import json

from gitvow.install import _body_digest, install_repo, place_policy, stamp_default
from gitvow.policy import DEFAULT_POLICY_PATH


def _default_text():
    with open(DEFAULT_POLICY_PATH) as fh:
        return fh.read()


def test_fresh_copy_is_stamped_and_a_rerun_changes_nothing(repo):
    p = repo / ".gitvow" / "policy.json"
    msg = place_policy(str(p), str(repo))
    assert msg.startswith("default policy")
    data = json.loads(p.read_text())
    assert data["_from_default"] == _body_digest(data)
    before = p.read_text()
    assert place_policy(str(p), str(repo)) is None and p.read_text() == before


def test_untouched_copy_of_an_older_default_is_refreshed(repo):
    p = repo / ".gitvow" / "policy.json"
    p.parent.mkdir()
    old = json.loads(_default_text())
    old["bash_confirm"] = old["bash_confirm"][1:]  # an older default: one rule fewer
    old.pop("_doc_commit_window", None)
    p.write_text(stamp_default(json.dumps(old)))
    msg = place_policy(str(p), str(repo))
    assert msg and "refreshed" in msg
    now = json.loads(p.read_text())
    assert len(now["bash_confirm"]) == len(json.loads(_default_text())["bash_confirm"])
    assert now["_from_default"] == _body_digest(now)


def test_edited_copy_and_legacy_copy_are_left_alone(repo):
    p = repo / ".gitvow" / "policy.json"
    p.parent.mkdir()
    edited = json.loads(stamp_default(_default_text()))
    edited["bash_deny"].append({"pattern": "\\bmy-tool\\s+wipe\\b", "reason": "our own rule"})
    p.write_text(json.dumps(edited, indent=2) + "\n")
    msg = place_policy(str(p), str(repo))
    assert msg and "edited after it was copied" in msg
    assert any(r.get("reason") == "our own rule" for r in json.loads(p.read_text())["bash_deny"])
    legacy = json.loads(_default_text())  # a copy from before the marker existed
    p.write_text(json.dumps(legacy, indent=2) + "\n")
    msg = place_policy(str(p), str(repo))
    assert msg and "predates the marker" in msg
    assert "_from_default" not in json.loads(p.read_text())


def test_install_repo_reports_the_policy_outcome(repo, home):
    lines = install_repo(str(repo))
    assert any(ln.startswith("default policy") for ln in lines)
    lines = install_repo(str(repo))
    assert any(ln.startswith("policy → ") for ln in lines)
