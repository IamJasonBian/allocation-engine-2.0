"""What if: shock scenarios and the worst historical windows, replayed on today's book.

Two families:
- Hypothetical shocks — a uniform move applied to every position (signed, so a
  short gains in a sell-off), plus asset-class shocks (crypto-only crash).
- Historical replays — the worst 1-day and worst 5-day joint return vectors
  actually observed, applied to current notionals. These are the scenarios
  the data has already shown the book can see.
"""

from app.risk._math import r

CRYPTO = {"BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LTC", "BCH", "LINK", "DOT", "UNI"}


def _apply(book: dict[str, dict], shock_by_symbol: dict[str, float]) -> dict:
    legs = []
    total = 0.0
    for sym, b in sorted(book.items()):
        if abs(b["notional"]) < 1e-9:
            continue
        shock = shock_by_symbol.get(sym, 0.0)
        pnl = b["notional"] * shock
        total += pnl
        legs.append({"symbol": sym, "shockPct": r(shock * 100, 2), "pnlUsd": r(pnl, 2)})
    return {"pnlUsd": r(total, 2), "legs": legs}


def stress_scenarios(returns: dict[str, list[float]], dates: list[str], book: dict[str, dict]) -> dict:
    syms = [s for s in book if abs(book[s]["notional"]) > 1e-9]
    gross = sum(abs(book[s]["notional"]) for s in syms)
    scenarios = []

    for pct in (-0.05, -0.10, -0.20):
        res = _apply(book, {s: pct for s in syms})
        scenarios.append({"name": f"Everything {int(pct * 100)}%", "kind": "hypothetical", **res})
    crypto_syms = [s for s in syms if s in CRYPTO]
    if crypto_syms:
        res = _apply(book, {s: -0.30 for s in crypto_syms})
        scenarios.append({"name": "Crypto -30%, rest flat", "kind": "hypothetical", **res})
    res = _apply(book, {s: 0.10 for s in syms})
    scenarios.append({"name": "Everything +10%", "kind": "hypothetical", **res})

    aligned = [s for s in syms if s in returns and returns[s]]
    if aligned:
        n = min(len(returns[s]) for s in aligned)
        daily = [sum(book[s]["notional"] * returns[s][t] for s in aligned) for t in range(n)]
        if daily:
            t = min(range(n), key=lambda i: daily[i])
            res = _apply(book, {s: returns[s][t] for s in aligned})
            scenarios.append({
                "name": f"Worst observed day ({dates[t + 1]})", "kind": "historical",
                "date": dates[t + 1], **res,
            })
        if n >= 5:
            windows = [(i, sum(daily[i:i + 5])) for i in range(n - 4)]
            i, _ = min(windows, key=lambda x: x[1])
            shock = {s: _compound(returns[s][i:i + 5]) for s in aligned}
            res = _apply(book, shock)
            scenarios.append({
                "name": f"Worst 5-day window ({dates[i + 1]} → {dates[i + 5]})", "kind": "historical",
                "start": dates[i + 1], "end": dates[i + 5], **res,
            })

    for sc in scenarios:
        sc["pnlPctOfGross"] = r(sc["pnlUsd"] / gross * 100, 2) if gross else 0.0
    return {"grossExposureUsd": r(gross, 2), "scenarios": scenarios}


def _compound(rs: list[float]) -> float:
    g = 1.0
    for x in rs:
        g *= 1 + x
    return g - 1
