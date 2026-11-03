"""options_positions must read the live mark (RH returns [[{...}]] for market data)."""

from app.brokers.robinhood_client import RobinhoodTrader


def test_mark_price_read_from_nested_market_data(monkeypatch):
    rh = "app.brokers.robinhood_client.rh."
    monkeypatch.setattr(rh + "options.get_open_option_positions", lambda: [{
        "chain_symbol": "NBIS", "type": "long", "quantity": "31.0000",
        "average_price": "208.0368", "trade_value_multiplier": "100",
        "option": "https://x/options/instruments/abc/", "option_id": "abc"}])
    monkeypatch.setattr(rh + "options.get_option_instrument_data_by_id",
                        lambda _id: {"strike_price": "230.0000",
                                     "expiration_date": "2026-11-20", "type": "call"})
    monkeypatch.setattr(rh + "options.get_option_market_data",
                        lambda *a, **k: [[{"mark_price": "0.955", "delta": "0.06"}]])
    t = RobinhoodTrader.__new__(RobinhoodTrader)
    t.account_number, t._authenticated = "TEST-ACCT", True
    monkeypatch.setattr(t, "_ensure_auth", lambda: None)
    pos = t.options_positions()
    assert len(pos) == 1
    assert round(pos[0]["purchase_price"], 4) == 2.0804
    assert pos[0]["mark_price"] == 0.955


def test_mark_fallback_is_logged(monkeypatch, caplog):
    import logging
    rh = "app.brokers.robinhood_client.rh."
    monkeypatch.setattr(rh + "options.get_open_option_positions", lambda: [{
        "chain_symbol": "NBIS", "type": "long", "quantity": "1", "average_price": "200",
        "option": "https://x/options/instruments/abc/", "option_id": "abc"}])
    monkeypatch.setattr(rh + "options.get_option_instrument_data_by_id",
                        lambda _id: {"strike_price": "230", "expiration_date": "2026-11-20", "type": "call"})

    def boom(*a, **k):
        raise RuntimeError("503 from marketdata")

    monkeypatch.setattr(rh + "options.get_option_market_data", boom)
    t = RobinhoodTrader.__new__(RobinhoodTrader)
    t.account_number, t._authenticated = "TEST-ACCT", True
    monkeypatch.setattr(t, "_ensure_auth", lambda: None)
    with caplog.at_level(logging.WARNING):
        pos = t.options_positions()
    assert pos[0]["mark_price"] == 2.0 and pos[0]["mark_ok"] is False
    assert "option mark unavailable for NBIS" in caplog.text and "503 from marketdata" in caplog.text


def _trader(monkeypatch):
    t = RobinhoodTrader.__new__(RobinhoodTrader)
    t.account_number, t._authenticated = "TEST-ACCT", True
    monkeypatch.setattr(t, "_ensure_auth", lambda: None)
    return t


def test_positions_fetch_none_or_error_raises(monkeypatch):
    import pytest
    from app.brokers.base import BrokerReadError
    rh = "app.brokers.robinhood_client.rh."
    t = _trader(monkeypatch)
    monkeypatch.setattr(rh + "options.get_open_option_positions", lambda: None)
    with pytest.raises(BrokerReadError):
        t.options_positions()

    def boom():
        raise RuntimeError("timeout")

    monkeypatch.setattr(rh + "options.get_open_option_positions", boom)
    with pytest.raises(BrokerReadError):
        t.options_positions()
    monkeypatch.setattr(rh + "options.get_open_option_positions", lambda: [])
    assert t.options_positions() == []


def test_instrument_lookup_failure_skips_position(monkeypatch, caplog):
    import logging
    rh = "app.brokers.robinhood_client.rh."
    monkeypatch.setattr(rh + "options.get_open_option_positions", lambda: [
        {"chain_symbol": "NBIS", "type": "long", "quantity": "1", "average_price": "200",
         "option": "https://x/options/instruments/bad/", "option_id": "bad"},
        {"chain_symbol": "MU", "type": "long", "quantity": "1", "average_price": "300",
         "option": "https://x/options/instruments/good/", "option_id": "good"}])

    def lookup(oid):
        if oid == "bad":
            raise RuntimeError("404")
        return {"strike_price": "900", "expiration_date": "2026-11-20", "type": "call"}

    monkeypatch.setattr(rh + "options.get_option_instrument_data_by_id", lookup)
    monkeypatch.setattr(rh + "options.get_option_market_data",
                        lambda *a, **k: [[{"mark_price": "3.5"}]])
    t = _trader(monkeypatch)
    with caplog.at_level(logging.WARNING):
        pos = t.options_positions()
    assert [p["option_id"] for p in pos] == ["good"]
    assert pos[0]["mark_ok"] is True
    assert "option_id=bad" in caplog.text
