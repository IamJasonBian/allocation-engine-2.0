"""Average-cost P&L engine: PnlSnapshot replayed over chronological fills.

Realized P&L is computed by streaming fills in time order through a running
average open price per symbol (average-cost method, not FIFO). A fill that
extends the position re-blends the average; a fill against the position
realizes P&L versus the average cost at that moment. Position flips
(close-and-open) reset the average to the flip price.
"""

from datetime import date, datetime, timedelta, timezone
from math import sqrt
from statistics import mean, stdev, variance


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


def today_walk(
    book: list[dict],
    fills: list[dict],
    day: date,
    *,
    skip_symbols: frozenset[str] = frozenset({"BTC.SHADOW"}),
) -> dict:
    """EOD recon for one calendar day: live qty minus that day's signed fills.

    `book` rows need `symbol`, `quantity` (or `qty`), and `current_price`.
    `fills` are `{symbol, side, qty, ts}`. Start-of-day qty is
    `nowQty - Σ signed fills on day` (BUY +, SELL −). Synthetic names like
    BTC.SHADOW are dropped.
    """
    day_s = day.isoformat() if isinstance(day, date) else str(day)[:10]
    signed: dict[str, float] = {}
    counts: dict[str, int] = {}
    for f in fills:
        if _fill_date(f["ts"]) != day_s:
            continue
        sym = str(f["symbol"]).upper()
        q = float(f["qty"])
        signed[sym] = signed.get(sym, 0.0) + (q if f["side"] == "BUY" else -q)
        counts[sym] = counts.get(sym, 0) + 1
    out = {}
    for row in book:
        sym = str(row["symbol"]).upper()
        if sym in skip_symbols:
            continue
        now = float(row.get("quantity") if row.get("quantity") is not None else row.get("qty") or 0)
        last = float(row.get("current_price") or row.get("last") or 0)
        net = signed.get(sym, 0.0)
        sod = now - net
        out[sym] = {
            "date": day_s,
            "sodQty": sod,
            "nowQty": now,
            "fills": counts.get(sym, 0),
            "netFillQty": net,
            "last": last,
            "position": abs(now) * last,
            "sodPosition": abs(sod) * last,
        }
    return out


def risk_from_today_walk(
    walk: dict,
    closes: list[float],
    dates: list[str] | None = None,
) -> dict:
    """Ticker risk scaled to live book qty from `today_walk`, not fill replay."""
    qty = float(walk["nowQty"])
    last = float(walk.get("last") or (closes[-1] if closes else 0))
    out = {
        "position": qty,
        **ticker_risk_model(closes),
    }
    if "growthRateStdPct" in out:
        out["riskUsd"] = round(abs(qty) * last * out["growthRateStdPct"] / 100, 2)
    if dates and len(dates) == len(closes):
        path = position_series([
            {"date": d, "close": c, "position": qty} for d, c in zip(dates, closes)
        ])
        out["variance"] = path["variance"]
        out["monthlyGrowth"] = path["monthlyGrowth"]
        out["positionSeries"] = path["daily"]
    return out


def position_series(mark: list[dict]) -> dict:
    """Daily USD position held from an engine mark series.

    Input rows are `_mark_series` output: `{date, close, position}` where
    `position` is signed share count. Output `position` is `|shares| × close`;
    `positionReturn` is the simple day-to-day change (None on day 1 or flat).
    Variance is sample variance of those returns; monthly growth is last/first
    position USD in each calendar month.
    """
    daily = []
    prev_usd = None
    returns: list[float] = []
    monthly_first: dict[str, float] = {}
    monthly_last: dict[str, float] = {}
    for p in sorted(mark, key=lambda r: r["date"]):
        usd = abs(float(p["position"])) * float(p["close"])
        ret = None
        if usd > 1e-12 and prev_usd is not None and prev_usd > 1e-12:
            ret = usd / prev_usd - 1
            returns.append(ret)
        if usd > 1e-12:
            prev_usd = usd
            month = str(p["date"])[:7]
            monthly_first.setdefault(month, usd)
            monthly_last[month] = usd
        daily.append({
            "date": p["date"],
            "close": float(p["close"]),
            "position": usd,
            "positionReturn": ret,
        })
    monthly = [
        {
            "month": m,
            "growthRatePct": round((monthly_last[m] / monthly_first[m] - 1) * 100, 4),
        }
        for m in monthly_first
    ]
    return {
        "daily": daily,
        "variance": variance(returns) if len(returns) >= 2 else None,
        "monthlyGrowth": monthly,
    }


def ticker_risk_model(closes: list[float]) -> dict:
    """Ticker risk: price stdev and standardized daily growth rates.

    Growth rate is `close_t / close_t-1 - 1`. Standardization is the
    sample z-score `(g - mean(g)) / stdev(g)`. `growthRateZ` is the
    latest day; `growthRateZSeries` is the full z path.
    """
    if len(closes) < 2:
        return {}
    growth = [cur / prev - 1 for prev, cur in zip(closes, closes[1:])]
    out = {"closeStdUsd": round(stdev(closes), 4)}
    if len(growth) < 2:
        gr_pct = round(growth[0] * 100, 4)
        out["growthRatePct"] = gr_pct
        out["growthRateMeanPct"] = gr_pct
        return out
    mu = mean(growth)
    sig = stdev(growth)
    z_series = (
        [0.0] * len(growth) if sig < 1e-12
        else [(g - mu) / sig for g in growth]
    )
    downside = sqrt(sum(min(g, 0) ** 2 for g in growth) / len(growth))
    gr_pct = round(mu * 100, 4)
    out.update({
        "growthRatePct": gr_pct,
        "growthRateMeanPct": gr_pct,
        "growthRateStdPct": round(sig * 100, 4),
        "riskAdjustedGrowthRate": round(mu / sig, 4) if sig >= 1e-12 else 0.0,
        "growthRateZ": round(z_series[-1], 4),
        "growthRateZSeries": [round(z, 4) for z in z_series],
        "downsideGrowthRateStdPct": round(downside * 100, 4),
    })
    return out


def _ticker_vol(closes: list[float]) -> dict:
    return ticker_risk_model(closes)


def pnl_risk(fills: list[dict], closes: list[dict], symbol: str) -> dict:
    """Per-ticker volatility with a position-scaled USD risk contribution.

    Price volatility is the sample std of daily closes; growth-rate
    volatility is the sample std of `close_t / close_t-1 - 1`.
    `riskUsd` is the 1σ daily dollar move:
    `|position| × last close × stdev(growth rates)`.
    """
    series = _mark_series(fills, closes, symbol)
    position = series[-1]["position"] if series else 0
    last_close = series[-1]["close"] if series else 0
    out = {
        "method": "additive_ticker_vol",
        "series": series,
        "observations": len(series),
        "position": position,
        **_ticker_vol([p["close"] for p in series]),
    }
    if "growthRateStdPct" in out:
        out["riskUsd"] = round(
            abs(position) * last_close * out["growthRateStdPct"] / 100, 2
        )
    path = position_series(series)
    out["variance"] = path["variance"]
    out["monthlyGrowth"] = path["monthlyGrowth"]
    out["positionSeries"] = path["daily"]
    return out


def format_ticker_risk(symbol: str, risk: dict) -> str:
    """Telegram-ready one-ticker risk line. Missing vol → insufficient closes."""
    if "closeStdUsd" not in risk:
        return f"{symbol}: insufficient closes"
    lines = [
        f"{symbol} risk",
        f"pos {risk['position']}",
        f"σ ${risk['riskUsd']}",
        f"close σ ${risk['closeStdUsd']}",
        f"GR {risk['growthRatePct']}%",
        f"RA GR {risk['riskAdjustedGrowthRate']}",
        f"GR σ {risk['growthRateStdPct']}%",
    ]
    if risk.get("variance") is not None:
        lines.append(f"var {risk['variance']:.6f}")
    return "\n".join(lines)


def format_portfolio_risk(portfolio: dict) -> str:
    """Telegram-ready whole-book 1σ line."""
    n = len(portfolio.get("symbols") or [])
    return (
        f"portfolio σ ${portfolio['riskUsd']}\n"
        f"{n} ticker{'s' if n != 1 else ''}"
    )


def portfolio_risk(fills: list[dict], closes_by_symbol: dict[str, list[dict]]) -> dict:
    """Whole-book 1σ: sum of per-ticker `|position| × last close × GR σ`.

    Tickers without enough closes for a std are omitted. `totalRiskUsd` is
    the additive (perfectly correlated) upper bound; `uncorrelatedRiskUsd`
    is sqrt of the sum of squares. True portfolio std lies between.
    """
    tickers = []
    for sym in sorted(closes_by_symbol):
        risk = pnl_risk(fills, closes_by_symbol[sym], sym)
        if "riskUsd" not in risk:
            continue
        tickers.append({
            "symbol": sym.upper(),
            "position": risk["position"],
            "closeStdUsd": risk["closeStdUsd"],
            "growthRateStdPct": risk["growthRateStdPct"],
            "downsideGrowthRateStdPct": risk["downsideGrowthRateStdPct"],
            "riskUsd": risk["riskUsd"],
        })
    return {
        "method": "additive_ticker_vol_portfolio",
        "symbols": [t["symbol"] for t in tickers],
        "tickers": tickers,
        "totalRiskUsd": round(sum(t["riskUsd"] for t in tickers), 2),
        "uncorrelatedRiskUsd": round(sqrt(sum(t["riskUsd"] ** 2 for t in tickers)), 2),
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
