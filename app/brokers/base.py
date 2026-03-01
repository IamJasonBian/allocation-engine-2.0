"""Abstract broker interface — all broker implementations must satisfy this protocol."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrokerClient(Protocol):
    """Interface that all broker implementations must satisfy."""

    def account(self) -> dict:
        """Return account summary: equity, cash, buying_power, portfolio_value."""
        ...

    def positions(self) -> list[dict]:
        """Return list of position dicts with standardized keys:
        symbol, qty, side, market_value, avg_entry, unrealized_pl, unrealized_pl_pct
        """
        ...

    def open_orders(self) -> list[dict]:
        """Return list of open order dicts with standardized keys:
        id, symbol, side, qty, type, limit_price, stop_price, status
        """
        ...

    def submit_order(self, order: dict) -> dict | None:
        """Submit an order. Returns order confirmation dict or None on failure."""
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order by ID."""
        ...

    def cancel_all(self) -> None:
        """Cancel all open orders."""
        ...
