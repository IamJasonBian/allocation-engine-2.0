"""Daily mark-to-market equity curve for the whole book.

Each symbol is replayed on its own close calendar (`pnl._mark_series`), then
summed on the union of dates; a symbol without a close on a date carries its
last mark forward. The curve is P&L-based (the engine has no cash ledger), so
"returns" are daily P&L over prior-day gross exposure — the return on capital
actually at risk, not on an account balance.
"""

from app.pnl import _mark_series
from app.risk._math import r


def per_symbol_marks(fills: list[dict], closes_by_symbol: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """{SYMBOL: [{date, close, position, totalPnl}, ...]} for symbols with closes."""
    out: dict[str, list[dict]] = {}
    for sym, closes in closes_by_symbol.items():
        marks = _mark_series(fills, closes, sym)
        if marks:
            out[sym.upper()] = marks
    return out


def portfolio_curve(marks_by_symbol: dict[str, list[dict]]) -> list[dict]:
    """Union-calendar book curve.

    Returns:
        [{date, totalPnl, dailyPnl, grossExposure, netExposure, longExposure,
          shortExposure, returnOnExposure}], ascending by date. `dailyPnl` and
        `returnOnExposure` are 0 on the first day; `returnOnExposure` is 0 on
        days whose prior-day exposure was flat.
    """
    dates = sorted({p["date"] for marks in marks_by_symbol.values() for p in marks})
    cursor = dict.fromkeys(marks_by_symbol, 0)
    curve: list[dict] = []
    for day in dates:
        pnl = long_exp = short_exp = 0.0
        for sym, marks in marks_by_symbol.items():
            i = cursor[sym]
            while i + 1 < len(marks) and marks[i + 1]["date"] <= day:
                i += 1
            cursor[sym] = i
            point = marks[i]
            if point["date"] > day:  # no close yet for this symbol
                continue
            pnl += point["totalPnl"]
            notional = point["position"] * point["close"]
            if notional >= 0:
                long_exp += notional
            else:
                short_exp += -notional
        curve.append({
            "date": day,
            "totalPnl": r(pnl, 2),
            "longExposure": r(long_exp, 2),
            "shortExposure": r(short_exp, 2),
            "grossExposure": r(long_exp + short_exp, 2),
            "netExposure": r(long_exp - short_exp, 2),
        })

    prev = None
    for point in curve:
        if prev is None:
            point["dailyPnl"] = 0.0
            point["returnOnExposure"] = 0.0
        else:
            delta = point["totalPnl"] - prev["totalPnl"]
            point["dailyPnl"] = r(delta, 2)
            point["returnOnExposure"] = (
                r(delta / prev["grossExposure"], 6) if prev["grossExposure"] > 1e-9 else 0.0
            )
        prev = point
    return curve


def aligned_returns(marks_by_symbol: dict[str, list[dict]]) -> tuple[list[str], dict[str, list[float]]]:
    """Daily growth rates per symbol on the intersection of close dates.

    Inner-join alignment: a covariance estimate needs every symbol observed on
    the same days. Returns (dates, {SYMBOL: [r_t, ...]}) with len(r) == len(dates) - 1.
    """
    if not marks_by_symbol:
        return [], {}
    common = None
    for marks in marks_by_symbol.values():
        ds = {p["date"] for p in marks}
        common = ds if common is None else common & ds
    dates = sorted(common or [])
    out: dict[str, list[float]] = {}
    for sym, marks in marks_by_symbol.items():
        close_by_date = {p["date"]: p["close"] for p in marks}
        closes = [close_by_date[d] for d in dates]
        out[sym] = [cur / prev - 1 for prev, cur in zip(closes, closes[1:])]
    return dates, out


def current_book(marks_by_symbol: dict[str, list[dict]]) -> dict[str, dict]:
    """Latest position, close, and USD notional per symbol."""
    book: dict[str, dict] = {}
    for sym, marks in marks_by_symbol.items():
        last = marks[-1]
        book[sym] = {
            "position": last["position"],
            "lastClose": last["close"],
            "notional": r(last["position"] * last["close"], 2),
            "asOf": last["date"],
        }
    return book
