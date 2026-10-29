"""Resolve a long_call recipe against a chain snapshot.

The intent on the bus is identity + recipe. This module is the executor
re-read: pick the ~0.22Δ call in the DTE window, require a live book, and
price a bid-side day limit. Quotes never go on Pub/Sub.
"""

from __future__ import annotations

import math

from app.pricing.black_scholes import (
    R,
    bs_call_delta,
    bs_call_implied_vol,
    bs_call_price,
)


def _tick_floor(px: float) -> float:
    tick = 0.05 if px >= 3.0 else 0.01
    return math.floor(px / tick + 1e-9) * tick


def resolve_long_call(chain: dict, recipe: dict) -> dict | None:
    dte = int(chain.get("dte") or 0)
    rule = recipe.get("dte_rule") or {}
    if dte < int(rule.get("min", 21)) or dte > int(rule.get("max", 35)):
        return None
    target = float((recipe.get("strike_rule") or {}).get("delta") or 0.22)
    lo, hi = target - 0.04, target + 0.06
    s = float(chain["spot"])
    t = dte / 365.0
    best = None
    best_score = -1.0
    for row in chain.get("calls") or []:
        last = float(row["last"])
        vol = int(row.get("vol") or 0)
        oi = int(row.get("oi") or 0)
        if last <= 0 or vol < 50:
            continue
        iv = bs_call_implied_vol(last, s, float(row["k"]), t, R)
        if iv is None:
            continue
        delta = bs_call_delta(s, float(row["k"]), t, iv, R)
        if not (lo <= delta <= hi):
            continue
        score = oi * vol
        if score > best_score:
            best_score = score
            theo = bs_call_price(s, float(row["k"]), t, iv, R)
            best = {**row, "delta": delta, "iv": iv, "theo": theo}
    if best is None:
        return None
    limit = _tick_floor(0.98 * min(float(best["last"]), float(best["theo"])))
    return {
        "symbol": chain["symbol"],
        "expiry": chain["expiry"],
        "strike": int(best["k"]) if float(best["k"]).is_integer() else float(best["k"]),
        "right": "call",
        "contracts": int(recipe.get("contracts") or 1),
        "limit": round(limit, 2),
        "last": float(best["last"]),
        "delta": best["delta"],
        "iv": best["iv"],
        "time_in_force": recipe.get("time_in_force", "day"),
        "order_type": "limit",
        "trigger": "immediate",
    }
