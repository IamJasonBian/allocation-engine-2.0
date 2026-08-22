"""What the book is right now: notionals, weights, concentration."""

from app.risk._math import r


def exposure(book: dict[str, dict]) -> dict:
    """Gross/net/long/short exposure, per-symbol weights, and concentration.

    Args:
        book: {SYMBOL: {position, lastClose, notional}} from `curve.current_book`.

    Returns:
        Weights are |notional| / gross. `hhi` is the Herfindahl index of those
        weights (1.0 = one position); `effectiveN` = 1 / hhi is "how many
        equal-sized positions this book behaves like".
    """
    rows = []
    long_exp = short_exp = 0.0
    for sym, b in sorted(book.items()):
        notional = b["notional"]
        if abs(notional) < 1e-9:
            continue
        if notional > 0:
            long_exp += notional
        else:
            short_exp += -notional
        rows.append({"symbol": sym, "position": b["position"], "lastClose": b["lastClose"], "notional": notional})
    gross = long_exp + short_exp
    for row in rows:
        row["weightPct"] = r(abs(row["notional"]) / gross * 100, 2) if gross else 0.0
        row["side"] = "long" if row["notional"] > 0 else "short"
    weights = [abs(x["notional"]) / gross for x in rows] if gross else []
    hhi = sum(w * w for w in weights)
    largest = max(rows, key=lambda x: abs(x["notional"]), default=None)
    return {
        "positions": rows,
        "openPositions": len(rows),
        "longExposureUsd": r(long_exp, 2),
        "shortExposureUsd": r(short_exp, 2),
        "grossExposureUsd": r(gross, 2),
        "netExposureUsd": r(long_exp - short_exp, 2),
        "netToGrossPct": r((long_exp - short_exp) / gross * 100, 2) if gross else 0.0,
        "hhi": r(hhi, 4),
        "effectiveN": r(1 / hhi, 2) if hhi else 0.0,
        "largestPosition": largest["symbol"] if largest else None,
        "largestPositionPct": largest["weightPct"] if largest else 0.0,
    }
