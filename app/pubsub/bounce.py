"""Session bounce-sleeve intents. Recipes only; executor prices later.

ordering_key = rh:{SYM}. event_id = intent:{key}:{session}:option_buy:{sit|place}.
Quotes stay off this bus. RebalancerObserver stays off GCP.
"""

from __future__ import annotations

BOUNCE = ("MU", "AVGO", "NET")
SIT = ("NBIS",)

RECIPE = {
    "action": "place",
    "asset_type": "option",
    "side": "buy",
    "right": "call",
    "structure": "long_call",
    "dte_rule": {"min": 21, "max": 35},
    "strike_rule": {"kind": "delta", "delta": 0.22},
    "contracts": 1,
    "time_in_force": "day",
    "order_type": "limit",
    "trigger": "immediate",
}


def publish_bounce_session(bus, *, session_date: str) -> None:
    for symbol in SIT:
        key = f"rh:{symbol}"
        fence = f"{key}:{session_date}:option_buy"
        event_id = f"intent:{fence}:sit"
        bus.publish(
            "intents",
            {
                "action": "sit",
                "symbol": symbol,
                "ref_id": event_id,
                "cause_event_id": fence,
                "session_fence": fence,
            },
            event_id=event_id,
            ordering_key=key,
            source="reducer",
        )
    for symbol in BOUNCE:
        key = f"rh:{symbol}"
        fence = f"{key}:{session_date}:option_buy"
        event_id = f"intent:{fence}:place"
        bus.publish(
            "intents",
            {
                **RECIPE,
                "symbol": symbol,
                "ref_id": event_id,
                "cause_event_id": fence,
                "session_fence": fence,
            },
            event_id=event_id,
            ordering_key=key,
            source="reducer",
        )
