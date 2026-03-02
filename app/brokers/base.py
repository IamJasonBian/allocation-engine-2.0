"""Abstract broker interface — all broker implementations must satisfy this protocol."""

from typing import Protocol, runtime_checkable

from app.models import AccountSummary, OpenOrder, Order, OrderResult, Position


@runtime_checkable
class BrokerClient(Protocol):
    """Interface that all broker implementations must satisfy."""

    def account(self) -> AccountSummary:
        """Return account summary: equity, cash, buying_power, portfolio_value."""
        ...

    def positions(self) -> list[Position]:
        """Return list of current positions."""
        ...

    def open_orders(self) -> list[OpenOrder]:
        """Return list of open orders on the broker."""
        ...

    def submit_order(self, order: Order) -> OrderResult | None:
        """Submit an order. Returns order confirmation or None on failure."""
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order by ID."""
        ...

    def cancel_all(self) -> None:
        """Cancel all open orders."""
        ...
