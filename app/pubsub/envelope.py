"""Encode a pub/sub envelope. Schema validate before any send."""

from __future__ import annotations

from datetime import datetime, timezone

TOPICS = frozenset({"fills", "orders", "marks", "intents", "control"})


class EncodeError(ValueError):
    """Event could not be encoded. Nothing was sent."""


def encode(topic, event, *, event_id, ordering_key, source="rh-poll",
           produced_at=None):
    if topic not in TOPICS:
        raise EncodeError(f"unknown topic {topic!r}")
    if not event_id:
        raise EncodeError("event_id is required")
    if not ordering_key or ":" not in str(ordering_key):
        raise EncodeError("ordering_key must be {broker}:{symbol}")
    if not isinstance(event, dict):
        raise EncodeError("event must be an object")
    ts = produced_at or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")
    return {
        "topic": topic,
        "schema": f"{topic}.v1",
        "event_id": str(event_id),
        "ordering_key": str(ordering_key),
        "produced_at": ts,
        "source": source,
        "event": dict(event),
    }
