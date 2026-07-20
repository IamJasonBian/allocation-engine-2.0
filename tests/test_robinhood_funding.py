"""Offline tests for RobinhoodTrader's ACH deposit/withdraw wrappers.

These call through the same box-injected robin_stocks session as every
other RobinhoodTrader method (no separate login), so tests just stub
_ensure_auth and mock the underlying robin_stocks.robinhood.account calls.
"""

from unittest import mock

import pytest
import robin_stocks.robinhood as rh

from app.brokers.robinhood_client import RobinhoodTrader


def make_trader():
    trader = object.__new__(RobinhoodTrader)
    trader.email = "trader@example.com"
    trader.account_number = ""
    trader._authenticated = True
    trader._ensure_auth = lambda: None
    return trader


def test_deposit_requires_ach_relationship():
    trader = make_trader()
    with pytest.raises(ValueError):
        trader.deposit(100)


def test_deposit_success():
    trader = make_trader()
    with mock.patch.object(rh.account, "deposit_funds_to_robinhood_account",
                            return_value={"id": "d1", "state": "pending"}) as m:
        result = trader.deposit(100, ach_relationship="ach-1")
    m.assert_called_once_with("ach-1", 100)
    assert result == {"id": "d1", "amount": 100.0, "state": "pending"}


def test_deposit_failure_returns_none():
    trader = make_trader()
    with mock.patch.object(rh.account, "deposit_funds_to_robinhood_account", return_value=None):
        assert trader.deposit(100, ach_relationship="ach-1") is None


def test_withdraw_requires_ach_relationship():
    trader = make_trader()
    with pytest.raises(ValueError):
        trader.withdraw(50)


def test_withdraw_success():
    trader = make_trader()
    with mock.patch.object(rh.account, "withdrawl_funds_to_bank_account",
                            return_value={"id": "w1", "state": "pending"}) as m:
        result = trader.withdraw(50, ach_relationship="ach-1")
    m.assert_called_once_with("ach-1", 50)
    assert result == {"id": "w1", "amount": 50.0, "state": "pending"}


def test_withdraw_failure_returns_none():
    trader = make_trader()
    with mock.patch.object(rh.account, "withdrawl_funds_to_bank_account", return_value=None):
        assert trader.withdraw(50, ach_relationship="ach-1") is None


def test_linked_bank_accounts_maps_fields():
    trader = make_trader()
    raw = [{"id": "b1", "bank_name": "Chase", "type": "checking", "verified": True}]
    with mock.patch.object(rh.account, "get_linked_bank_accounts", return_value=raw):
        accounts = trader.linked_bank_accounts()
    assert accounts == [{"id": "b1", "bank_name": "Chase", "account_type": "checking", "verified": True}]


def test_transfer_history_maps_fields():
    trader = make_trader()
    raw = [{"id": "t1", "amount": "25.00", "direction": "deposit",
            "state": "completed", "created_at": "2026-01-01"}]
    with mock.patch.object(rh.account, "get_bank_transfers", return_value=raw) as m:
        history = trader.transfer_history(direction="deposit")
    m.assert_called_once_with(direction="deposit")
    assert history[0]["amount"] == 25.0
    assert history[0]["direction"] == "deposit"
