"""Average-cost P&L engine: PnlSnapshot replayed over chronological fills.

Realized P&L is computed by streaming fills in time order through a running
average open price per symbol (average-cost method, not FIFO). A fill that
extends the position re-blends the average; a fill against the position
realizes P&L versus the average cost at that moment. Position flips
(close-and-open) reset the average to the flip price.
"""

from datetime import date, datetime, timedelta, timezone
from math import sqrt
from statistics import stdev


class PnlSnapshot:
    """Running P&L state for one symbol under the average-cost method."""

    def __init__(self, ticker: str):
        self.m_ticker = ticker
        self.m_net_position = 0.0
        self.m_avg_open_price = 0.0
        self.m_net_investment = 0.0
        self.m_realized_pnl = 0.0
        self.m_unrealized_pnl = 0.0
        self.m_total_pnl = 0.0

    # buy_or_sell: 1 is buy, 2 is sell
    def update_by_tradefeed(
        self, buy_or_sell: int, traded_price: float, traded_quantity: float
    ) -> float:
        """Apply one fill and return the realized P&L delta it produced.

        Args:
            buy_or_sell: 1 for buy, 2 for sell.
            traded_price: Average fill price.
            traded_quantity: Filled quantity (positive).

        Returns:
            Realized P&L from this fill (0.0 for position-extending fills).
        """
        # buy: positive position, sell: negative position
        quantity_with_direction = (
            traded_quantity if buy_or_sell == 1 else -traded_quantity
        )
        is_still_open = (self.m_net_position * quantity_with_direction) >= 0
        # net investment
        self.m_net_investment = max(
            self.m_net_investment, abs(self.m_net_position * self.m_avg_open_price)
        )
        # realized pnl
        realized_delta = 0.0
        if not is_still_open:
            # Keep the sign of the net position being reduced
            realized_delta = (
                (traded_price - self.m_avg_open_price)
                * min(abs(quantity_with_direction), abs(self.m_net_position))
                * (abs(self.m_net_position) / self.m_net_position)
            )
            self.m_realized_pnl += realized_delta
        # avg open price
        if is_still_open:
            self.m_avg_open_price = (
                (self.m_avg_open_price * self.m_net_position)
                + (traded_price * quantity_with_direction)
            ) / (self.m_net_position + quantity_with_direction)
        elif traded_quantity > abs(self.m_net_position):
            # close-and-open: flipped through zero, basis restarts at this fill
            self.m_avg_open_price = traded_price
        # net position
        self.m_net_position += quantity_with_direction
        self.m_total_pnl = self.m_realized_pnl + self.m_unrealized_pnl
        return realized_delta

    def update_by_marketdata(self, last_price: float) -> None:
        """Mark the open position to `last_price` and refresh unrealized/total."""
        self.m_unrealized_pnl = (
            last_price - self.m_avg_open_price
        ) * self.m_net_position
        self.m_total_pnl = self.m_realized_pnl + self.m_unrealized_pnl


def replay_fills(fills: list[dict]) -> tuple[dict, list[dict]]:
    """Replay fills chronologically through per-symbol PnlSnapshots.

    Basis is only correct when `fills` is the complete history for each
    symbol — feed everything, then window on the returned realize events.

    Args:
        fills: Dicts with keys symbol, side ("BUY"/"SELL"), qty, price, ts
            (ts must be sortable — datetime or ISO string).

    Returns:
        (snapshots, realize_events): snapshots maps symbol -> PnlSnapshot
        after the full replay; realize_events is a chronological list of
        {"symbol", "ts", "realized"} for every fill that realized P&L.
    """
    snapshots: dict[str, PnlSnapshot] = {}
    realize_events: list[dict] = []
    for fill in sorted(fills, key=lambda f: f["ts"]):
        snap = snapshots.setdefault(fill["symbol"], PnlSnapshot(fill["symbol"]))
        delta = snap.update_by_tradefeed(
            1 if fill["side"] == "BUY" else 2, fill["price"], fill["qty"]
        )
        if delta:
            realize_events.append(
                {"symbol": fill["symbol"], "ts": fill["ts"], "realized": delta}
            )
    return snapshots, realize_events


def _in_window(
    ts: datetime,
    *,
    cutoff: datetime | None,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if cutoff is not None and ts < cutoff:
        return False
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def _window_stats(fills: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for f in fills:
        s = stats.setdefault(f["symbol"], {
            "bought_qty": 0.0, "bought_vol": 0.0, "buys": 0,
            "sold_qty": 0.0, "sold_vol": 0.0, "sells": 0,
        })
        if f["side"] == "BUY":
            s["bought_qty"] += f["qty"]
            s["bought_vol"] += f["qty"] * f["price"]
            s["buys"] += 1
        else:
            s["sold_qty"] += f["qty"]
            s["sold_vol"] += f["qty"] * f["price"]
            s["sells"] += 1
    return stats


def compute_pnl(
    fills: list[dict],
    *,
    days: int | None = 30,
    start: datetime | None = None,
    end: datetime | None = None,
    mark_prices: dict[str, float] | None = None,
    now: datetime | None = None,
) -> dict:
    """Average-cost P&L from normalized fills.

    Replay the full fill history for correct basis, then window realized
    events and activity stats. Pass mark_prices to include unrealized/total
    on the current open book.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = None if start or end or days is None else now - timedelta(days=days)

    snapshots, realize_events = replay_fills(fills)

    if mark_prices:
        for sym, snap in snapshots.items():
            if snap.m_net_position and sym in mark_prices:
                snap.update_by_marketdata(mark_prices[sym])

    realized_by_symbol: dict[str, float] = {}
    for ev in realize_events:
        if _in_window(ev["ts"], cutoff=cutoff, start=start, end=end):
            realized_by_symbol[ev["symbol"]] = (
                realized_by_symbol.get(ev["symbol"], 0.0) + ev["realized"]
            )

    window_fills = [
        f for f in fills
        if _in_window(f["ts"], cutoff=cutoff, start=start, end=end)
    ]
    stats = _window_stats(window_fills)

    symbols = set(stats) | set(realized_by_symbol)
    if mark_prices:
        symbols |= {
            sym for sym, snap in snapshots.items() if snap.m_net_position
        }

    symbols_pnl = []
    total_realized = 0.0
    total_unrealized = 0.0

    for sym in sorted(symbols):
        s = stats.get(sym, {
            "bought_qty": 0.0, "bought_vol": 0.0, "buys": 0,
            "sold_qty": 0.0, "sold_vol": 0.0, "sells": 0,
        })
        realized = realized_by_symbol.get(sym, 0.0)
        snap = snapshots.get(sym)
        unrealized = snap.m_unrealized_pnl if snap else 0.0

        total_realized += realized
        total_unrealized += unrealized

        row = {
            "symbol": sym,
            "realizedPnL": round(realized, 2),
            "totalBought": round(s["bought_vol"], 2),
            "totalSold": round(s["sold_vol"], 2),
            "buyCount": s["buys"],
            "sellCount": s["sells"],
            "avgBuyPrice": round(s["bought_vol"] / s["bought_qty"], 4) if s["bought_qty"] else 0,
            "avgSellPrice": round(s["sold_vol"] / s["sold_qty"], 4) if s["sold_qty"] else 0,
            "netPosition": round(snap.m_net_position, 4) if snap else 0,
            "avgOpenPrice": round(snap.m_avg_open_price, 4) if snap else 0,
            "remainingShares": round(snap.m_net_position, 4) if snap else 0,
        }
        if mark_prices:
            row["unrealizedPnL"] = round(unrealized, 2)
            row["totalPnL"] = round(realized + unrealized, 2)
            if sym in mark_prices:
                row["markPrice"] = round(mark_prices[sym], 4)

        symbols_pnl.append(row)

    out = {
        "totalRealizedPnL": round(total_realized, 2),
        "totalBuyVolume": round(sum(s["bought_vol"] for s in stats.values()), 2),
        "totalSellVolume": round(sum(s["sold_vol"] for s in stats.values()), 2),
        "method": "avg_cost_full_history",
        "symbols": symbols_pnl,
    }
    if days is not None and not start and not end:
        out["days"] = days
    if start:
        out["start"] = start.isoformat()
    if end:
        out["end"] = end.isoformat()
    if mark_prices:
        out["totalUnrealizedPnL"] = round(total_unrealized, 2)
        out["totalPnL"] = round(total_realized + total_unrealized, 2)

    return out


def _fill_date(ts) -> str:
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    if isinstance(ts, date):
        return ts.isoformat()
    return str(ts)[:10]


def _mark_series(fills: list[dict], closes: list[dict], symbol: str) -> list[dict]:
    """Daily mark-to-market series for one symbol: fills replayed day by day,
    open position marked to each daily close."""
    sym = symbol.upper()
    sym_fills = sorted(
        (f for f in fills if f["symbol"].upper() == sym), key=lambda f: f["ts"]
    )
    closes = sorted(closes, key=lambda c: c["date"])

    snap = PnlSnapshot(sym)
    series: list[dict] = []
    i = 0
    for row in closes:
        day, close = row["date"], float(row["close"])
        while i < len(sym_fills) and _fill_date(sym_fills[i]["ts"]) <= day:
            f = sym_fills[i]
            snap.update_by_tradefeed(
                1 if f["side"] == "BUY" else 2, f["price"], f["qty"]
            )
            i += 1
        snap.update_by_marketdata(close)
        series.append({
            "date": day,
            "close": close,
            "position": round(snap.m_net_position, 8),
            "totalPnl": round(snap.m_total_pnl, 2),
        })
    return series


def _std_stats(dates: list[str], deltas: list[float], returns: list[float]) -> dict:
    """Std of daily P&L deltas / returns, annualized by sampling density."""
    out: dict = {"observations": len(deltas)}
    if len(deltas) >= 2:
        daily_std_pct = stdev(returns) * 100
        # Sampling frequency implies the annualization calendar: near-daily
        # closes (crypto) => 365 periods/year, trading-day closes => 252.
        span_days = (
            date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])
        ).days or 1
        periods_per_year = 365 if len(dates) / span_days > 0.9 else 252
        out.update({
            "dailyStdUsd": round(stdev(deltas), 2),
            "dailyStdPct": round(daily_std_pct, 4),
            "annualizedStdPct": round(daily_std_pct * sqrt(periods_per_year), 2),
            "periodsPerYear": periods_per_year,
        })
    return out


def pnl_risk(fills: list[dict], closes: list[dict], symbol: str) -> dict:
    """Daily mark-to-market P&L series for one symbol, with std as the risk measure.

    Replays fills day by day and marks the open position to each daily close,
    so volatility while holding is captured — not just P&L at trade instants.
    Std is computed over day-to-day P&L deltas, restricted to days with an
    open position (flat days are zero by construction and would dilute it).
    Percent returns are P&L delta over prior-day market exposure.
    """
    series = _mark_series(fills, closes, symbol)

    deltas: list[float] = []
    returns: list[float] = []
    for prev, cur in zip(series, series[1:]):
        exposure = abs(prev["position"]) * prev["close"]
        if exposure < 1e-9:
            continue
        delta = cur["totalPnl"] - prev["totalPnl"]
        deltas.append(delta)
        returns.append(delta / exposure)

    return {
        "method": "daily_mark_to_market",
        "series": series,
        **_std_stats([p["date"] for p in series], deltas, returns),
    }


def portfolio_risk(fills: list[dict], closes_by_symbol: dict[str, list[dict]]) -> dict:
    """Portfolio volatility: per-symbol mark-to-market P&L summed per day.

    Each symbol is marked on its own close calendar, then summed on the union
    of dates; a symbol without a close on a given date carries its last mark
    forward. Percent returns divide the daily P&L delta by prior-day gross
    exposure (sum of |position| x close across symbols).
    """
    per_symbol: dict[str, list[dict]] = {}
    for sym, closes in closes_by_symbol.items():
        marks = _mark_series(fills, closes, sym)
        if marks:
            per_symbol[sym.upper()] = marks

    dates = sorted({p["date"] for marks in per_symbol.values() for p in marks})
    cursor = dict.fromkeys(per_symbol, 0)
    series: list[dict] = []
    for day in dates:
        pnl = 0.0
        exposure = 0.0
        for sym, marks in per_symbol.items():
            i = cursor[sym]
            while i + 1 < len(marks) and marks[i + 1]["date"] <= day:
                i += 1
            cursor[sym] = i
            point = marks[i]
            if point["date"] > day:  # symbol has no close yet
                continue
            pnl += point["totalPnl"]
            exposure += abs(point["position"]) * point["close"]
        series.append({
            "date": day,
            "totalPnl": round(pnl, 2),
            "grossExposure": round(exposure, 2),
        })

    deltas: list[float] = []
    returns: list[float] = []
    for prev, cur in zip(series, series[1:]):
        if prev["grossExposure"] < 1e-9:
            continue
        delta = cur["totalPnl"] - prev["totalPnl"]
        deltas.append(delta)
        returns.append(delta / prev["grossExposure"])

    return {
        "method": "daily_mark_to_market_portfolio",
        "symbols": sorted(per_symbol),
        "series": series,
        **_std_stats(dates, deltas, returns),
    }


def cost_basis_series(fills: list[dict], symbol: str) -> list[dict]:
    """Return avg open price after each fill for one symbol (for charts)."""
    sym = symbol.upper()
    snap = PnlSnapshot(sym)
    series: list[dict] = []
    for fill in sorted(
        (f for f in fills if f["symbol"].upper() == sym),
        key=lambda f: f["ts"],
    ):
        snap.update_by_tradefeed(
            1 if fill["side"] == "BUY" else 2, fill["price"], fill["qty"]
        )
        ts = fill["ts"]
        series.append({
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else ts,
            "side": fill["side"],
            "qty": fill["qty"],
            "fillPrice": round(fill["price"], 4),
            "avgOpenPrice": round(snap.m_avg_open_price, 4),
            "netPosition": round(snap.m_net_position, 4),
        })
    return series
