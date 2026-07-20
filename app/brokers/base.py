"""Abstract broker interface — all broker implementations must inherit from this ABC."""

from abc import ABC, abstractmethod


class BrokerClient(ABC):
    """Base class that all broker implementations must subclass."""

    @abstractmethod
    def account(self) -> dict:
        """Return account summary: equity, cash, buying_power, portfolio_value."""
        pass

    @abstractmethod
    def positions(self) -> list[dict]:
        """Return list of position dicts with standardized keys:
        symbol, qty, side, market_value, avg_entry, unrealized_pl, unrealized_pl_pct
        """
        pass

    @abstractmethod
    def open_orders(self) -> list[dict]:
        """Return list of open order dicts with standardized keys:
        id, symbol, side, qty, type, limit_price, stop_price, status
        """
        pass

    @abstractmethod
    def submit_order(self, order: dict) -> dict | None:
        """Submit an order. Returns order confirmation dict or None on failure."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order by ID."""
        pass

    @abstractmethod
    def cancel_all(self) -> None:
        """Cancel all open orders."""
        pass

    # -- funding: linked-bank deposit/withdraw (optional) --------------------
    # Not every broker supports ACH transfer initiation via API; the default
    # implementations below raise so callers get a clean, catchable error
    # instead of an AttributeError. Brokers that do support it override these.

    def linked_bank_accounts(self) -> list[dict]:
        """Return linked bank accounts available for ACH transfer."""
        raise NotImplementedError(f"{type(self).__name__} does not support linked bank accounts")

    def deposit(self, amount: float, **kwargs) -> dict | None:
        """Deposit funds from a linked bank account into the broker."""
        raise NotImplementedError(f"{type(self).__name__} does not support deposits")

    def withdraw(self, amount: float, **kwargs) -> dict | None:
        """Withdraw funds from the broker to a linked bank account."""
        raise NotImplementedError(f"{type(self).__name__} does not support withdrawals")

    def transfer_history(self, **kwargs) -> list[dict]:
        """Return past bank transfers (deposits and withdrawals)."""
        raise NotImplementedError(f"{type(self).__name__} does not support transfer history")
