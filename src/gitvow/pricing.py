"""Token pricing: a dated default table of list prices per million tokens, overridable from the policy."""

from __future__ import annotations

from typing import Any

PRICING_LABEL = "gitvow defaults 2026-09"

# USD per million tokens. List prices as published by vendors; users override under policy["pricing"].
DEFAULTS: dict[str, dict[str, float]] = {
    "claude-fable-5-1": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-5": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus-4": {"input": 15, "output": 75, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet-5": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    "claude-sonnet-4": {"input": 3, "output": 15, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku-4": {"input": 1, "output": 5, "cache_read": 0.1, "cache_write": 1.25},
    "gpt-5": {"input": 1.25, "output": 10, "cache_read": 0.125},
    "gpt-4.1": {"input": 2, "output": 8, "cache_read": 0.5},
    "o3": {"input": 2, "output": 8, "cache_read": 0.5},
    "gemini-2.5-pro": {"input": 1.25, "output": 10, "cache_read": 0.31},
    "gemini-2.5-flash": {"input": 0.3, "output": 2.5, "cache_read": 0.075},
    "gemini-3": {"input": 2, "output": 12, "cache_read": 0.5},
}

KINDS = ("input", "output", "cache_read", "cache_write", "reasoning")


def table(pol: dict[str, Any] | None) -> tuple[dict[str, dict[str, float]], str]:
    override = (pol or {}).get("pricing") or {}
    if not isinstance(override, dict) or not override:
        return DEFAULTS, PRICING_LABEL
    merged = {**DEFAULTS}
    for k, v in override.items():
        if isinstance(v, dict):
            merged[str(k)] = {kk: float(vv) for kk, vv in v.items() if kk in KINDS}
    return merged, f"{PRICING_LABEL} + policy overrides"


def price_for(model: str, tbl: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """Exact name first, then the longest prefix match (claude-fable matches claude-fable-5-1)."""
    if model in tbl:
        return tbl[model]
    best = None
    for k in tbl:
        if (model.startswith(k) or k.startswith(model)) and (best is None or len(k) > len(best)):
            best = k
    return tbl[best] if best else None


def estimate(usage: dict[str, Any], pol: dict[str, Any] | None) -> dict[str, Any]:
    """Add estimated_cost_usd, pricing and unpriced_models to a usage dict with per-model token buckets."""
    tbl, label = table(pol)
    cost = 0.0
    unpriced: list[str] = []
    for model, buckets in (usage.get("by_model") or {}).items():
        p = price_for(model, tbl)
        if not p:
            unpriced.append(model)
            continue
        cost += buckets.get("input", 0) / 1e6 * p.get("input", 0)
        cost += buckets.get("output", 0) / 1e6 * p.get("output", 0)
        cost += buckets.get("reasoning", 0) / 1e6 * p.get("output", 0)  # reasoning is billed as output where reported
        cost += buckets.get("cache_read", 0) / 1e6 * p.get("cache_read", p.get("input", 0))
        cost += buckets.get("cache_write", 0) / 1e6 * p.get("cache_write", p.get("input", 0))
    out = dict(usage)
    out["estimated_cost_usd"] = round(cost, 4)
    out["pricing"] = label
    if unpriced:
        out["unpriced_models"] = sorted(unpriced)
    return out
