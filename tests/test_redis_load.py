"""Redis load check — INFO probe + hash sizes, logged for the orders sync."""

import logging

from app import redis_store


class FakeRedis:
    def __init__(self, info=None, hlens=None, raise_on_info=False):
        self._info = info or {}
        self._hlens = hlens or {}
        self._raise_on_info = raise_on_info
        self.closed = False

    def info(self):
        if self._raise_on_info:
            raise RuntimeError("boom")
        return self._info

    def hlen(self, key):
        return self._hlens.get(key, 0)

    def close(self):
        self.closed = True


def test_returns_none_when_no_client(monkeypatch):
    monkeypatch.setattr(redis_store, "_get_client", lambda: None)
    assert redis_store.log_redis_load() is None


def test_collects_and_derives_metrics(monkeypatch, caplog):
    fake = FakeRedis(
        info={
            "used_memory": 50,
            "maxmemory": 200,
            "used_memory_human": "50B",
            "connected_clients": 3,
            "instantaneous_ops_per_sec": 12,
            "keyspace_hits": 90,
            "keyspace_misses": 10,
            "mem_fragmentation_ratio": 1.2,
        },
        hlens={"stocks": 7, "orders": 4},
    )
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)

    with caplog.at_level(logging.INFO):
        metrics = redis_store.log_redis_load()

    assert metrics["used_memory_pct"] == 25.0
    assert metrics["hit_rate_pct"] == 90.0
    assert metrics["stocks_keys"] == 7
    assert metrics["orders_keys"] == 4
    assert fake.closed
    assert "[redis-load]" in caplog.text


def test_handles_missing_maxmemory_and_no_keyspace(monkeypatch):
    fake = FakeRedis(info={"used_memory": 50, "maxmemory": 0})
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)

    metrics = redis_store.log_redis_load()

    assert metrics["used_memory_pct"] is None
    assert metrics["hit_rate_pct"] is None


def test_returns_none_and_closes_on_error(monkeypatch):
    fake = FakeRedis(raise_on_info=True)
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)

    assert redis_store.log_redis_load() is None
    assert fake.closed
