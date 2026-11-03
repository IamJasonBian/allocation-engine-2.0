"""Single-purpose lifecycle manager for ONE option vertical-spread order.

Run as its own Render background worker (``python -m app.spread_manager``).
Scope is enforced in code, not by convention:

  * it acts only on the order id in SPREAD_ORDER_ID, and refuses to run unless
    that order's two legs are exactly the instruments in SPREAD_LONG_ID /
    SPREAD_SHORT_ID (a mismatch raises SandboxViolation and the loop stops);
  * it can only place *closing* credit orders for those two legs, never more
    than the filled quantity, and can only cancel closing orders it can see
    on those same legs;
  * it has no inbound HTTP surface, and authenticates to Robinhood solely
    through the auth-service box (RobinhoodTrader, no local login);
  * SPREAD_MANAGER_DRY_RUN defaults to true: it logs what it would do.

Lifecycle: wait for the fill -> (hold through the earnings print until
SPREAD_TP_NOT_BEFORE_ET) -> rest a GTC take-profit credit at
SPREAD_TP_PCT_OF_MAX% of max profit -> on expiry day, from the guard time,
replace it with a closing order priced at the spread bid (re-priced each
pass), and from the hard time sell for any bid, so the position is never left
to Robinhood's 3:30 PM ET expiry sellout. State is re-derived from Robinhood
on every pass, so a restart is safe.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from zoneinfo import ZoneInfo

log = logging.getLogger("spread_manager")
ET = ZoneInfo("America/New_York")
OPEN_STATES = {"queued", "confirmed", "partially_filled", "pending", "unconfirmed"}
DEAD_STATES = {"cancelled", "canceled", "rejected", "failed", "voided"}


class SandboxViolation(RuntimeError):
    """The managed order does not match the configured, allowed spread."""


@dataclass(frozen=True)
class SpreadSpec:
    order_id: str
    long_id: str
    short_id: str
    symbol: str
    expiration: date
    long_strike: float
    short_strike: float
    option_type: str = "call"

    @property
    def width(self) -> float:
        return abs(self.short_strike - self.long_strike)


@dataclass(frozen=True)
class Config:
    tp_pct_of_max: float = 50.0
    guard_time: dtime = dtime(9, 45)
    hard_time: dtime = dtime(12, 30)
    dry_run: bool = True
    # Hold through the earnings print: no take-profit is rested before this time.
    tp_not_before: datetime | None = None


def tick_round(price: float) -> float:
    """Round to Robinhood's option tick (0.05 at/above $3, else 0.01)."""
    if price >= 3:
        return round(round(price / 0.05) * 0.05, 2)
    return round(price, 2)


def take_profit_credit(fill_debit: float, width: float, pct_of_max: float) -> float:
    """Closing credit that captures ``pct_of_max``% of the spread's max profit."""
    return tick_round(fill_debit + (width - fill_debit) * pct_of_max / 100.0)


def validate(spec: SpreadSpec, order: dict) -> None:
    """Raise SandboxViolation unless ``order`` is exactly the allowed spread."""
    if order.get("id") != spec.order_id:
        raise SandboxViolation("order id mismatch")
    legs = order.get("legs") or []
    seen = {}
    for leg in legs:
        oid = str(leg.get("option", "")).rstrip("/").rsplit("/", 1)[-1] or leg.get("option_id")
        seen[oid] = (leg.get("side"), leg.get("position_effect"))
    want = {spec.long_id: ("buy", "open"), spec.short_id: ("sell", "open")}
    if seen != want or order.get("direction") != "debit":
        raise SandboxViolation(f"legs do not match allowed spread: {seen}")


def step(gw, spec: SpreadSpec, cfg: Config, now: datetime, memo: dict | None = None) -> str:
    """One management pass. Returns a short status string; ``done:*`` ends the job.

    ``memo`` remembers the id of the closing order this process placed, so a
    blind or wrong open-order listing can never cause a duplicate placement.
    """
    memo = {} if memo is None else memo
    now = now.astimezone(ET)
    if now.date() > spec.expiration:
        return "done:expired"
    order = gw.order(spec.order_id)
    validate(spec, order)
    state = str(order.get("state"))
    if state in DEAD_STATES:
        return "done:not_filled"
    if state != "filled":
        return "wait:fill"          # open/partial; GFD expires on its own

    qty = int(float(order.get("processed_quantity") or 0))
    if qty < 1:
        return "done:not_filled"
    open_qty = gw.open_qty(spec)
    if open_qty is None:
        return "wait:position_read"
    if open_qty >= 1:
        memo["saw_open_position"] = True
    elif memo.get("saw_open_position"):
        return "done:closed"
    else:
        return "wait:position"
    fill_debit = float(order["processed_premium"]) / (100.0 * qty)
    closes = gw.open_close_orders(spec)
    mid = memo.get("close_id")
    if mid:
        info = gw.order(mid)
        st = str(info.get("state"))
        if st in OPEN_STATES:
            if not any(c["id"] == mid for c in closes):
                closes = closes + [{"id": mid, "price": info.get("price")}]
        elif st == "filled":
            return "done:closed"
        else:
            memo.pop("close_id", None)

    on_expiry = now.date() == spec.expiration
    if on_expiry and now.time() >= cfg.hard_time:
        target, why = 0.05, "hard"
    elif on_expiry and now.time() >= cfg.guard_time:
        bid, _ask = gw.spread_quote(spec)
        target, why = max(0.05, tick_round(bid)), "guard"
    else:
        if cfg.tp_not_before and now < cfg.tp_not_before:
            return "hold:pre_report"
        target, why = take_profit_credit(fill_debit, spec.width, cfg.tp_pct_of_max), "tp"

    for c in closes:
        px = float(c.get("price") or 0)
        if why == "tp" or abs(px - target) < 0.005:
            return f"hold:{why}@{px:.2f}"
        if cfg.dry_run:
            log.info("[dry-run] would cancel close %s @%.2f to re-price %.2f (%s)",
                     c["id"], px, target, why)
            return f"dry:reprice:{why}"
        gw.cancel_close(spec, c["id"])
        memo.pop("close_id", None)
        closes = []
        break
    if closes:
        return f"hold:{why}"
    if cfg.dry_run:
        log.info("[dry-run] would place closing credit %.2f x%d (%s)", target, qty, why)
        return f"dry:place:{why}@{target:.2f}"
    live_qty = gw.open_qty(spec)
    if live_qty is None:
        return "wait:position_read"
    if live_qty < 1:
        return "wait:position" if not memo.get("saw_open_position") else "done:closed"
    oid = gw.place_close(spec, min(qty, live_qty), target)
    memo["close_id"] = oid
    return f"placed:{why}@{target:.2f}:{oid}"


class RHGateway:
    """Robinhood calls via robin_stocks, authenticated through the auth box."""

    def __init__(self, trader):
        self.trader = trader
        self.placed_ids: set[str] = set()

    def _rh(self):
        import robin_stocks.robinhood as rh
        self.trader._ensure_auth()
        return rh

    def order(self, order_id):
        return self._rh().orders.get_option_order_info(order_id)

    def open_close_orders(self, spec):
        ids = {spec.long_id, spec.short_id}
        out = []
        for o in self._rh().orders.get_all_open_option_orders() or []:
            legs = o.get("legs") or []
            leg_ids = {str(l.get("option", "")).rstrip("/").rsplit("/", 1)[-1] for l in legs}
            if leg_ids == ids and all(l.get("position_effect") == "close" for l in legs):
                out.append(o)
        return out

    def open_qty(self, spec):
        """Long-leg quantity, or ``None`` when Robinhood did not return a position list."""
        positions = self._rh().options.get_open_option_positions()
        if positions is None:
            return None
        for p in positions:
            if str(p.get("option_id")) == spec.long_id:
                return int(float(p.get("quantity") or 0))
        return 0

    def spread_quote(self, spec):
        rh = self._rh()

        def md(i):
            d = rh.options.get_option_market_data_by_id(i)
            d = d[0] if isinstance(d, list) else d
            return float(d["bid_price"]), float(d["ask_price"])

        (lb, la), (sb, sa) = md(spec.long_id), md(spec.short_id)
        return max(0.0, lb - sa), max(0.0, la - sb)

    def place_close(self, spec, qty, credit):
        legs = [
            {"expirationDate": spec.expiration.isoformat(), "strike": spec.long_strike,
             "optionType": spec.option_type, "effect": "close", "action": "sell",
             "ratio_quantity": 1},
            {"expirationDate": spec.expiration.isoformat(), "strike": spec.short_strike,
             "optionType": spec.option_type, "effect": "close", "action": "buy",
             "ratio_quantity": 1},
        ]
        res = self._rh().orders.order_option_spread(
            direction="credit", price=float(credit), symbol=spec.symbol,
            quantity=int(qty), spread=legs,
            account_number=self.trader.account_number or None,
            timeInForce="gtc", jsonify=True)
        if not res or not res.get("id"):
            raise RuntimeError(f"closing order rejected: {res}")
        self.placed_ids.add(res["id"])
        return res["id"]

    def cancel_close(self, spec, order_id):
        if order_id not in ({o["id"] for o in self.open_close_orders(spec)} | self.placed_ids):
            raise SandboxViolation("refusing to cancel an order that is not our closing order")
        self._rh().orders.cancel_option_order(order_id)


def spec_from_env(env=os.environ) -> SpreadSpec:
    return SpreadSpec(
        order_id=env["SPREAD_ORDER_ID"], long_id=env["SPREAD_LONG_ID"],
        short_id=env["SPREAD_SHORT_ID"], symbol=env["SPREAD_SYMBOL"].upper(),
        expiration=date.fromisoformat(env["SPREAD_EXPIRATION"]),
        long_strike=float(env["SPREAD_LONG_STRIKE"]),
        short_strike=float(env["SPREAD_SHORT_STRIKE"]),
        option_type=env.get("SPREAD_OPTION_TYPE", "call"))


def config_from_env(env=os.environ) -> Config:
    h = lambda k, d: dtime(*map(int, env.get(k, d).split(":")))
    return Config(
        tp_pct_of_max=float(env.get("SPREAD_TP_PCT_OF_MAX", "50")),
        guard_time=h("SPREAD_GUARD_TIME_ET", "09:45"),
        hard_time=h("SPREAD_HARD_TIME_ET", "12:30"),
        dry_run=env.get("SPREAD_MANAGER_DRY_RUN", "true").lower() != "false",
        tp_not_before=(datetime.fromisoformat(env["SPREAD_TP_NOT_BEFORE_ET"]).replace(tzinfo=ET)
                       if env.get("SPREAD_TP_NOT_BEFORE_ET") else None))


def _market_hours(now: datetime) -> bool:
    now = now.astimezone(ET)
    return now.weekday() < 5 and dtime(9, 30) <= now.time() < dtime(16, 0)


def run() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    spec, cfg = spec_from_env(), config_from_env()
    from app.brokers.robinhood_client import RobinhoodTrader
    gw = RHGateway(RobinhoodTrader())
    log.info("managing one spread order (dry_run=%s, tp=%.0f%% of max)",
             cfg.dry_run, cfg.tp_pct_of_max)
    last, memo = None, {}
    while True:
        now = datetime.now(ET)
        try:
            status = step(gw, spec, cfg, now, memo) if (_market_hours(now) or last is None) else last
        except SandboxViolation:
            log.exception("sandbox violation; stopping management")
            while True:
                time.sleep(3600)
        except Exception:                                  # noqa: BLE001
            log.exception("pass failed; will retry")
            status = last
        if status != last:
            log.info("status: %s", status)
        last = status
        if str(status).startswith("done:"):
            log.info("lifecycle complete (%s); idling", status)
            while True:
                time.sleep(3600)
        time.sleep(30 if _market_hours(now) else 300)


if __name__ == "__main__":
    run()
