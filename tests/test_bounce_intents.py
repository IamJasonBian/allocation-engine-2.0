"""Bounce-sleeve option_buy recipes on the existing intents topic.

NBIS sits (already long 6 calls). MU/AVGO/NET get long_call recipes, not
OCC symbols. Quotes stay off the bus.
"""

from app.pubsub import MockBus
from app.pubsub.bounce import publish_bounce_session


def test_session_publishes_sit_on_nbis_and_call_recipes_on_names():
    bus = MockBus()
    bus.subscribe("intents", "executor")
    publish_bounce_session(bus, session_date="2026-10-19")
    intents = bus.pull("executor")
    by_key = {env["ordering_key"]: env for env in intents}

    nbis = by_key["rh:NBIS"]
    assert nbis["event_id"] == "intent:rh:NBIS:2026-10-19:option_buy:sit"
    assert nbis["event"]["action"] == "sit"
    assert nbis["event"]["symbol"] == "NBIS"
    assert "strike" not in nbis["event"]

    for key in ("rh:MU", "rh:AVGO", "rh:NET"):
        env = by_key[key]
        ev = env["event"]
        assert ev["action"] == "place"
        assert ev["asset_type"] == "option"
        assert ev["right"] == "call"
        assert ev["structure"] == "long_call"
        assert ev["strike_rule"] == {"kind": "delta", "delta": 0.22}
        assert ev["dte_rule"] == {"min": 21, "max": 35}
        assert ev["contracts"] == 1
        assert ev["time_in_force"] == "day"
        assert ev["order_type"] == "limit"
        assert "strike" not in ev
        assert "limit" not in ev
        assert env["event_id"] == ev["ref_id"] == f"intent:{key}:2026-10-19:option_buy:place"

    # republish same fence is a no-op
    publish_bounce_session(bus, session_date="2026-10-19")
    assert bus.pull("executor") == []
