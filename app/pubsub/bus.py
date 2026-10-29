"""In-process mock bus. Same contract as the GCP publisher: event_id identity,
ordering_key is the entity, pull is at-least-once until ack.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count

from app.pubsub.envelope import encode


@dataclass(frozen=True)
class Published:
    id: str
    message_id: str | None


class MockBus:
    def __init__(self):
        self._subs: dict[str, str] = {}          # subscription -> topic
        self._log: dict[str, list[dict]] = {}    # topic -> envelopes
        self._ids: dict[tuple[str, str], str] = {}  # (topic, event_id) -> message_id
        self._cursors: dict[str, int] = {}       # subscription -> next index
        self._seq = count(1)

    def subscribe(self, topic, name):
        self._subs[name] = topic
        self._log.setdefault(topic, [])
        self._cursors.setdefault(name, 0)

    def subscriptions(self):
        return dict(self._subs)

    def publish(self, topic, event, *, event_id, ordering_key, source="rh-poll"):
        env = encode(topic, event, event_id=event_id, ordering_key=ordering_key,
                     source=source)
        key = (topic, env["event_id"])
        existing = self._ids.get(key)
        if existing is not None:
            return Published(id=env["event_id"], message_id=existing)
        message_id = f"mock-{next(self._seq)}"
        env = {**env, "message_id": message_id}
        self._log.setdefault(topic, []).append(env)
        self._ids[key] = message_id
        return Published(id=env["event_id"], message_id=message_id)

    def pull(self, subscription, max_messages=100):
        topic = self._subs[subscription]
        log = self._log.get(topic, [])
        start = self._cursors.get(subscription, 0)
        batch = log[start:start + max_messages]
        self._cursors[subscription] = start + len(batch)
        return [dict(m) for m in batch]
