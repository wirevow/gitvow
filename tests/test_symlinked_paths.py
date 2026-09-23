"""0.28.5: a finding names the repository-relative path even when the checkout is reached through a symlink.

Found by the real-harness check on macOS, where `/var/folders/…` is `/private/var/folders/…`: the agent's absolute
file path and the repository's resolved top disagreed, the relative path came out as `../../…`, and the finding
was recorded with the absolute path, so it could never match the same edit made from another shell.
"""

import json
import os

from gitvow.hooks import pre_tool_use, session_start
from gitvow.policy import DEFAULT_POLICY_PATH, evaluate


def test_finding_path_is_relative_through_a_symlinked_checkout(repo, home, payload, tmp_path):
    link = tmp_path / "link-to-repo"
    os.symlink(repo, link)
    (repo / "values" / "production-in").mkdir(parents=True)
    (repo / "values" / "production-in" / "app.yaml").write_text("replicas: 1\n")
    with open(DEFAULT_POLICY_PATH) as fh:
        pol = json.load(fh)
    # the agent names the file through the symlink; the policy is evaluated against the resolved repository
    d = evaluate(pol, "Edit", {"file_path": str(link / "values" / "production-in" / "app.yaml")}, str(repo))
    assert d.findings[0]["finding"] == "edit values/production-in/app.yaml"
    # and the other way round: the resolved file, the symlinked cwd
    d = evaluate(pol, "Edit", {"file_path": str(repo / "values" / "production-in" / "app.yaml")}, str(link))
    assert d.findings[0]["finding"] == "edit values/production-in/app.yaml"
    # through the hook, with the session rooted at the symlink
    p = payload("SessionStart")
    p["cwd"] = str(link)
    session_start(p, str(home))
    q = payload("PreToolUse", "Edit", {"file_path": str(link / "values" / "production-in" / "app.yaml")})
    q["cwd"] = str(link)
    pre_tool_use(q, str(home))
    st = json.loads((repo / ".git" / "gitvow-session.json").read_text())
    assert st["findings"][0]["finding"] == "edit values/production-in/app.yaml"
