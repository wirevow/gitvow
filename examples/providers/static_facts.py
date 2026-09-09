#!/usr/bin/env python3
"""Example gitvow provider over a static JSON facts file. Reads one request on stdin, writes one answer on stdout.

Facts file shape:
{
  "gate_bearing": ["auth/AuthorizeWhitelistedPaths.java", "deploy/values/production-*.yaml"],
  "whitelist_file": "auth/AuthorizeWhitelistedPaths.java",
  "authorized_routes": ["/v1/orders", "/v1/orders/*"],
  "callers": {"/v1/orders": ["client-orch (OrdersClient.java:88)"]}
}
"""

import argparse
import fnmatch
import json
import sys


def route_authorized(route, patterns):
    for p in patterns:
        if p.endswith("/*") and (route == p[:-2] or route.startswith(p[:-1])):
            return True
        if fnmatch.fnmatch(route, p) or route == p:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", required=True)
    a = ap.parse_args()
    with open(a.facts) as fh:
        facts = json.load(fh)
    req = json.load(sys.stdin)
    q, subject = req.get("question"), req.get("subject", "")
    if q == "gate_bearing":
        hit = [p for p in facts.get("gate_bearing", []) if fnmatch.fnmatch(subject, p) or subject.endswith(p)]
        out = {"answer": "yes" if hit else "no", "evidence": [f"listed as gate-bearing ({hit[0]})"] if hit else []}
    elif q == "route_gate":
        if route_authorized(subject, facts.get("authorized_routes", [])):
            out = {"answer": "no", "evidence": ["covered by authorized_routes"]}
        else:
            out = {
                "answer": "yes",
                "evidence": [
                    f"route {subject} is not covered by any authorization rule",
                    f"whitelist is {facts.get('whitelist_file', 'unknown')}",
                ],
            }
    elif q == "route_callers":
        callers = facts.get("callers", {}).get(subject, [])
        out = {"answer": "yes" if callers else "unknown", "evidence": [f"called by {c}" for c in callers]}
    else:
        out = {"answer": "unknown", "evidence": ["unsupported question"]}
    out["confidence"] = 1.0 if out["answer"] != "unknown" else 0.0
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
