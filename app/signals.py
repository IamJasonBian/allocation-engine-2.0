"""Buy/sell signals from external systems, routed through a strategy.

Any system may POST a signal; whether it can act is decided here, not by the
transport. Every signal is alerted on, and unauthorized ones never trade.

    AlertOnlyStrategy  — notify, never trade (unauthorized, or mode=alert)
    ExecuteStrategy    — notify, then submit the order if its guard passes:
                         SELL ≤ held qty (no shorts);
                         BUY needs a limit_price and qty × limit ≤ buying power
"""

from dataclasses import dataclass
from typing import Protocol

from app.slack import notify

SIDES = ("BUY", "SELL")


@dataclass(frozen=True)
class Signal:
    side: str
    symbol: str
    quantity: float
    source: str
    reason: str = ""
    limit_price: float | None = None
    authorized: bool = False


class SignalStrategy(Protocol):
    name: str

    def handle(self, signal: Signal) -> dict: ...


def _describe(s: Signal) -> str:
    price = f" @ {s.limit_price}" if s.limit_price is not None else " @ market"
    auth = "authorized" if s.authorized else "UNAUTHORIZED"
    why = f" — {s.reason}" if s.reason else ""
    return f"{s.side} {s.quantity} {s.symbol}{price} from {s.source} [{auth}]{why}"


class AlertOnlyStrategy:
    name = "alert_only"

    def __init__(self, why: str):
        self.why = why

    def handle(self, signal: Signal) -> dict:
        notify(f"[{signal.side.lower()}-signal] {_describe(signal)} → not executed ({self.why})")
        return {"status": "alerted", "executed": False, "why": self.why}


class ExecuteStrategy:
    name = "execute"

    def __init__(self, broker, dry_run: bool):
        self.broker = broker
        self.dry_run = dry_run

    def _guard(self, signal: Signal) -> str | None:
        """Return why the order must not be placed, or None if it may."""
        if signal.side == "SELL":
            held = sum(float(p.get("qty") or 0) for p in self.broker.positions()
                       if p.get("symbol") == signal.symbol)
            if signal.quantity > held:
                return f"qty {signal.quantity} > held {held}"  # never open a short
            return None
        if signal.limit_price is None:
            return "BUY signals need a limit_price"  # bound the cost up front
        cost = signal.quantity * signal.limit_price
        bp = float(self.broker.account().get("buying_power") or 0)
        if cost > bp:
            return f"cost {cost:.2f} > buying power {bp:.2f}"
        return None

    def handle(self, signal: Signal) -> dict:
        blocked = self._guard(signal)
        if blocked:
            return AlertOnlyStrategy(blocked).handle(signal)

        tag = f"[{signal.side.lower()}-signal]"
        order = {
            "symbol": signal.symbol,
            "side": signal.side,
            "quantity": signal.quantity,
            "order_type": "limit" if signal.limit_price is not None else "market",
            "limit_price": signal.limit_price,
            "stop_price": None,
            "market_hours": "regular_hours",
        }
        if self.dry_run:
            notify(f"{tag} {_describe(signal)} → simulated (dry_run)")
            return {"status": "simulated", "executed": False, "order": order}

        result = self.broker.submit_order(order)
        if result is None:
            notify(f"{tag} {_describe(signal)} → broker REJECTED", bypass_debounce=True)
            return {"status": "rejected", "executed": False, "order": order}
        notify(f"{tag} {_describe(signal)} → submitted {result.get('id')}",
               bypass_debounce=True)
        return {"status": "submitted", "executed": True,
                "orderId": result.get("id"), "order": order}


def select_strategy(signal: Signal, mode: str, broker_factory, dry_run: bool) -> SignalStrategy:
    """Pick the strategy for a signal.

    Args:
        signal: The incoming buy or sell signal.
        mode: SELL_SIGNAL_MODE — "alert" (default) or "execute"; applies to
            both sides.
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
