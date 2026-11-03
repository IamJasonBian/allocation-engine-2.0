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
