"""The http sink (0.24): a store served over HTTP receives the bundle part by part, idempotent by digest; a 4xx is
a refusal that is not queued; an unreachable server queues; the pack and the brief come back from the same server.
The store here is a stub speaking the protocol's verbs; the real one lives in the private server package."""

import hashlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from gitvow import pack as pk
from gitvow import sync as sy
from gitvow.policy import evaluate, load_policy
from tests.conftest import git

SOURCE = "github.com/acme/payments-api"


class StubStore(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, token):
        super().__init__(("127.0.0.1", 0), Stub)
        self.token = token
        self.pending = {}
        self.held = {}  # digest -> {"manifest":..., "parts": {...}, "attestation": ...}
        self.pack = None
        self.brief = None
        self.requests = []


class Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _ok(self):
        self.server.requests.append((self.command, self.path))
        if self.server.token and self.headers.get("Authorization") != f"Bearer {self.server.token}":
            self._body()
            self._send(401, {"error": "bearer token required"})
            return False
        return True

    def do_POST(self):
        if not self._ok():
            return
        body = self._body()
        if self.path == "/v1/bundles":
            m = json.loads(body)
            if m.get("kind") != "record":
                return self._send(400, {"error": "refused: only record"})
            d = m["digest"].split(":")[-1]
            assert self.headers["Idempotency-Key"].endswith(d)
            if d in self.server.held:
                return self._send(200, {"status": "already held", "digest": d})
            self.server.pending.setdefault(d, {"manifest": m, "parts": {}})
            return self._send(200, {"status": "new", "digest": d, "held": sorted(self.server.pending[d]["parts"])})
        if self.path.endswith("/commit"):
            d = self.path.split("/")[3]
            p = self.server.pending.get(d)
            if not p:
                return self._send(409, {"error": "begin first"})
            for part in p["manifest"]["parts"]:
                data = p["parts"].get(part["path"])
                if data is None or hashlib.sha256(data).hexdigest() != part["sha256"]:
                    return self._send(400, {"error": f"refused: part {part['path']} missing or wrong"})
            self.server.held[d] = {**p, "attestation": json.loads(body)}
            del self.server.pending[d]
            return self._send(200, {"status": "ingested", "digest": d, "commit": "abc1234"})
        self._send(404, {"error": "unknown verb"})

    def do_PUT(self):
        if not self._ok():
            return
        body = self._body()
        _, _, _, d, _, name = self.path.split("/")
        self.server.pending[d]["parts"][name] = body
        self._send(200, {"digest": d, "part": name})

    def do_GET(self):
        if not self._ok():
            return
        if self.path.startswith("/v1/rules"):
            return self._send(200, self.server.pack) if self.server.pack else self._send(404, {"error": "no pack"})
        if self.path.startswith("/v1/brief"):
            return self._send(200, self.server.brief) if self.server.brief else self._send(404, {"error": "none"})
        self._send(404, {"error": "unknown verb"})


def _serve(token="s3cret"):  # noqa: S107 - a test fixture, not a credential
    srv = StubStore(token)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _sinks(repo, rows):
    (repo / ".gitvow").mkdir(exist_ok=True)
    (repo / ".gitvow" / "export.local.json").write_text(json.dumps({"sinks": rows}))


def _decided(repo, name, trailer):
    (repo / f"{name}.txt").write_text(name + "\n")
    git(repo, "add", f"{name}.txt")
    git(repo, "commit", "-qm", f"{name}\n\nGitvow-Session: s1\nGitvow-Step: 1\n{trailer}\n")


def test_http_sink_delivers_part_by_part_and_fetches_pack_and_brief(repo, home, monkeypatch):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    _decided(repo, "c1", "Gitvow-Accepted: edit core/authz_rules.go by priya")
    srv, url = _serve()
    srv.pack = {
        "pack": 1,
        "from": ["acme-platform"],
        "expires": "2027-01-01",
        "rules": {"bash_deny": [{"pattern": r"\bdrop\b", "reason": "org"}]},
        "settings": {},
    }
    srv.brief = {
        "protocol": 1,
        "repo": SOURCE,
        "as_of": "2026-09-17T10:00:00Z",
        "stale_after": "2026-09-18T10:00:00Z",
        "classes": [],
        "claims": [],
        "gaps": [],
    }
    monkeypatch.setenv("WIREVOW_STORE_TOKEN", "s3cret")
    _sinks(repo, [{"name": "st", "type": "http", "url": url + "/", "token_env": "WIREVOW_STORE_TOKEN"}])
    s = sy.sinks(str(repo), str(home))
    assert s[0]["url"] == url and s[0]["path"] is None and "st: http " + url in sy.render_sinks(s)
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    r = out["results"][0]
    assert r["status"] == "delivered" and r["commit"] == "abc1234" and r["url"].endswith(r["digest"])
    assert r["fetched"] == {"pack": "fetched", "brief": "fetched"}
    held = srv.held[r["digest"]]
    assert set(held["parts"]) == {p["path"] for p in held["manifest"]["parts"]} and held["attestation"]["schema"]
    verbs = [(m, p.split("?")[0].split("/")[2] if p.count("/") > 2 else p) for m, p in srv.requests]
    assert verbs[0] == ("POST", "/v1/bundles") and ("POST", "bundles") in verbs
    assert any(m == "PUT" for m, _ in srv.requests)
    # the fetched pack is in force, the brief is cached
    pol = load_policy(str(repo), str(home))
    assert pol["_pack"]["applied"] and evaluate(pol, "Bash", {"command": "psql -c 'drop table x'"}).outcome == "deny"
    assert pk.load_brief(str(repo), str(home))["source"] == "cache"
    # second sync: already held after one request, nothing re-uploaded; pack and brief unchanged
    n = len(srv.requests)
    out2 = sy.sync(str(repo), str(home), since=None, rules=[])
    r2 = out2["results"][0]
    assert r2["status"] == "already held" and r2["fetched"] == {"pack": "unchanged", "brief": "unchanged"}
    assert [m for m, _ in srv.requests[n:]] == ["POST", "GET", "GET"]
    assert "st: already held" in sy.render(out2)
    srv.shutdown()


def test_http_refusal_is_reported_not_queued_and_unreachable_is_queued(repo, home, monkeypatch, tmp_path):
    git(repo, "remote", "add", "origin", "git@github.com:acme/payments-api.git")
    _decided(repo, "c1", "Gitvow-Declined: edit .github/workflows/ci.yml by dmitri")
    srv, url = _serve(token="right")
    _sinks(repo, [{"name": "st", "type": "http", "url": url, "token": "wrong"}])
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    r = out["results"][0]
    assert r["status"] == "refused" and "bearer token" in r["reason"] and "queued_at" not in r and "fetched" not in r
    assert not os.path.isdir(os.path.join(str(home), ".gitvow", "outbox", "st"))
    assert "(bearer token required)" in sy.render(out)
    port = srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    # the server is gone: unreachable, queued; a second record while down also waits
    _sinks(repo, [{"name": "st", "type": "http", "url": f"http://127.0.0.1:{port}", "token": "right"}])
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    assert out["results"][0]["status"] == "unreachable" and os.path.isdir(out["results"][0]["queued_at"])
    _decided(repo, "c2", "Gitvow-Accepted: edit core/authz_rules.go by priya")
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    assert out["results"][0]["status"] == "unreachable"
    box = os.path.join(str(home), ".gitvow", "outbox", "st", "github.com_acme_payments-api")
    assert len(os.listdir(box)) == 2
    # a server comes back on another port: the outbox drains oldest first, then the current record is already held
    srv2, url2 = _serve(token="right")
    _sinks(repo, [{"name": "st", "type": "http", "url": url2, "token": "right"}])
    out = sy.sync(str(repo), str(home), since=None, rules=[])
    kinds = [(x["status"], x.get("from_outbox", False)) for x in out["results"]]
    assert kinds[:2] == [("delivered", True), ("delivered", True)] and kinds[-1][0] == "already held"
    assert not os.listdir(box) and len(srv2.held) == 2
    assert out["results"][-1]["fetched"] == {"pack": "none", "brief": "none"}
    srv2.shutdown()


def test_http_sink_config_is_validated(repo, home):
    _sinks(repo, [{"name": "st", "type": "http", "path": "/nope"}])
    try:
        sy.sinks(str(repo), str(home))
        raise AssertionError
    except ValueError as e:
        assert "needs a url" in str(e)
    _sinks(repo, [{"name": "st", "type": "git"}])
    try:
        sy.sinks(str(repo), str(home))
        raise AssertionError
    except ValueError as e:
        assert "needs a path" in str(e)
