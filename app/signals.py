"""Sell signals from external systems, routed through a strategy.

Any system may POST a sell signal; whether it can act is decided here, not by
the transport. Every signal is alerted on, and unauthorized ones never trade.

    AlertOnlyStrategy  — notify, never trade (unauthorized, or mode=alert)
    ExecuteStrategy    — notify, then submit a SELL capped at the held qty
"""

from dataclasses import dataclass
from typing import Protocol

from app.slack import notify


@dataclass(frozen=True)
class SellSignal:
    symbol: str
    quantity: float
    source: str
    reason: str = ""
    limit_price: float | None = None
    authorized: bool = False


class SignalStrategy(Protocol):
    name: str

    def handle(self, signal: SellSignal) -> dict: ...


def _describe(s: SellSignal) -> str:
    price = f" @ {s.limit_price}" if s.limit_price is not None else " @ market"
    auth = "authorized" if s.authorized else "UNAUTHORIZED"
    why = f" — {s.reason}" if s.reason else ""
    return f"SELL {s.quantity} {s.symbol}{price} from {s.source} [{auth}]{why}"


class AlertOnlyStrategy:
    name = "alert_only"

    def __init__(self, why: str):
        self.why = why

    def handle(self, signal: SellSignal) -> dict:
        notify(f"[sell-signal] {_describe(signal)} → not executed ({self.why})")
        return {"status": "alerted", "executed": False, "why": self.why}


class ExecuteStrategy:
    name = "execute"

    def __init__(self, broker, dry_run: bool):
        self.broker = broker
        self.dry_run = dry_run

    def handle(self, signal: SellSignal) -> dict:
        held = sum(float(p.get("qty") or 0) for p in self.broker.positions()
                   if p.get("symbol") == signal.symbol)
        if signal.quantity > held:
            # Never let a signal open a short.
            return AlertOnlyStrategy(f"qty {signal.quantity} > held {held}").handle(signal)

        order = {
            "symbol": signal.symbol,
            "side": "SELL",
            "quantity": signal.quantity,
            "order_type": "limit" if signal.limit_price is not None else "market",
            "limit_price": signal.limit_price,
            "stop_price": None,
            "market_hours": "regular_hours",
        }
        if self.dry_run:
            notify(f"[sell-signal] {_describe(signal)} → simulated (dry_run)")
            return {"status": "simulated", "executed": False, "order": order}

        result = self.broker.submit_order(order)
        if result is None:
            notify(f"[sell-signal] {_describe(signal)} → broker REJECTED", bypass_debounce=True)
            return {"status": "rejected", "executed": False, "order": order}
        notify(f"[sell-signal] {_describe(signal)} → submitted {result.get('id')}",
               bypass_debounce=True)
        return {"status": "submitted", "executed": True,
                "orderId": result.get("id"), "order": order}


def select_strategy(signal: SellSignal, mode: str, broker_factory, dry_run: bool) -> SignalStrategy:
    """Pick the strategy for a signal.

    Args:
        signal: The incoming sell signal.
        mode: SELL_SIGNAL_MODE — "alert" (default) or "execute".
        broker_factory: Zero-arg callable returning the broker; only called
            when execution is possible.
        dry_run: Whether ExecuteStrategy should simulate.

    Returns:
        The strategy to run.
    """
    if not signal.authorized:
        return AlertOnlyStrategy("caller not authorized")
    if mode != "execute":
        return AlertOnlyStrategy("SELL_SIGNAL_MODE=alert")
    return ExecuteStrategy(broker_factory(), dry_run)
