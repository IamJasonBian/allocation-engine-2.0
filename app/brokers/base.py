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
