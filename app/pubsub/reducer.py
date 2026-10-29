"""Reduce ordered facts for one sleeve into the next intent.

Owns only mapped trailing-stop sells. One cover per key. Sit when the
resting qty already matches floor(Q * qty_fraction).
"""

from __future__ import annotations

from app.pnl import PnlSnapshot
from app.risk.equity_stop_mapping import (
    EQUITY_STOP_MAPPING,
    equity_stop_percent,
    equity_stop_qty_fraction,
)


class Reducer:
    def __init__(self, bus):
        self.bus = bus
        self._sleeves: dict[str, dict] = {}
        self._inbox: set[tuple[str, str]] = set()

    def seed(self, ordering_key, q):
        sleeve = self._sleeves.setdefault(
            ordering_key, {"q": 0.0, "owned_order_id": None, "owned_qty": 0})
        sleeve["q"] = float(q)

    def handle(self, envelope):
        topic = envelope["topic"]
        event_id = envelope["event_id"]
        if (topic, event_id) in self._inbox:
            return
        self._inbox.add((topic, event_id))

        ordering_key = envelope["ordering_key"]
        event = envelope["event"]
        sleeve = self._sleeves.setdefault(
            ordering_key, {"q": 0.0, "owned_order_id": None, "owned_qty": 0})
        symbol = event.get("symbol") or ordering_key.split(":")[-1]

        if topic == "fills":
            self._apply_fill(sleeve, event, symbol)
        elif topic == "orders":
            attached = self._attach(sleeve, event, symbol)
            oid = event.get("venue_order_id")
            if not attached and oid != sleeve["owned_order_id"]:
                return

        if symbol.upper() not in EQUITY_STOP_MAPPING:
            return

        frac = equity_stop_qty_fraction(symbol)
        trail = equity_stop_percent(symbol, 0)
        desired = int(sleeve["q"] * frac)
        if desired >= 1 and sleeve["owned_qty"] == desired:
            self._publish_sit(ordering_key, symbol, event_id, desired)
        elif desired >= 1 and sleeve["owned_order_id"] is None:
            self._publish_place(
                ordering_key, symbol, event_id, desired, frac, trail)

    def _apply_fill(self, sleeve, event, symbol):
        snap = sleeve.get("snap")
        if snap is None:
            snap = PnlSnapshot(symbol)
            sleeve["snap"] = snap
        buy_or_sell = 1 if str(event.get("side", "")).lower() == "buy" else 2
        snap.update_by_tradefeed(
            buy_or_sell, float(event["price"]), float(event["qty"]))
        sleeve["q"] = snap.m_net_position

    def _attach(self, sleeve, event, symbol):
        if event.get("state") != "confirmed":
            return False
        if event.get("side") != "sell" or event.get("trigger") != "stop":
            return False
        qty = int(float(event.get("qty") or 0))
        desired = int(sleeve["q"] * equity_stop_qty_fraction(symbol))
        if qty == desired and desired >= 1:
            sleeve["owned_order_id"] = event.get("venue_order_id")
            sleeve["owned_qty"] = qty
            return True
        return False

    def _publish_sit(self, ordering_key, symbol, cause_event_id, qty):
        event_id = f"intent:{cause_event_id}:sit"
        self.bus.publish(
            "intents",
            {
                "action": "sit",
                "symbol": symbol,
                "qty": qty,
                "ref_id": event_id,
                "cause_event_id": cause_event_id,
            },
            event_id=event_id,
            ordering_key=ordering_key,
            source="reducer",
        )

    def _publish_place(self, ordering_key, symbol, cause_event_id, qty,
                       qty_fraction, trail):
        event_id = f"intent:{cause_event_id}:place"
        self.bus.publish(
            "intents",
            {
                "action": "place",
                "symbol": symbol,
                "side": "sell",
                "type": "market",
                "trigger": "stop",
                "trail": trail,
                "qty_fraction": qty_fraction,
                "qty": qty,
                "market_hours": "regular_hours",
                "ref_id": event_id,
                "cause_event_id": cause_event_id,
            },
            event_id=event_id,
            ordering_key=ordering_key,
            source="reducer",
        )
