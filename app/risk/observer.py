"""Observer pattern base classes for risk event dispatch.

Follows the classic Subject/Observer ABC pattern:
  - Observer(ABC)  defines  @abstractmethod update(symbol, price)
  - Subject(ABC)   defines  @abstractmethod attach / detach / notify_observers
  - RiskSubject    is the concrete Subject that manages subscriptions + dispatch
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.enums import RiskEventType
from app.risk.events import RiskEvent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observer ABC
# ---------------------------------------------------------------------------

class RiskObserver(ABC):
    """Interface that all risk-event subscribers must implement."""

    @abstractmethod
    def update(self, symbol: str, price: float) -> None:
        """Called when a watched symbol's price changes."""
        pass

    @abstractmethod
    def on_risk_event(self, event: RiskEvent) -> None:
        """Called when a risk DQ event is emitted."""
        pass


# ---------------------------------------------------------------------------
# Subject ABC
# ---------------------------------------------------------------------------

class Subject(ABC):
    """Abstract subject (observable) that observers subscribe to."""

    @abstractmethod
    def attach(self, observer: RiskObserver, event_type: RiskEventType | None = None) -> None:
        pass

    @abstractmethod
    def detach(self, observer: RiskObserver) -> None:
        pass

    @abstractmethod
    def notify_observers(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Concrete Subject
# ---------------------------------------------------------------------------

class RiskSubject(Subject):
    """Concrete observable that dispatches RiskEvents to registered observers.

    Observers can subscribe globally or to specific event types.
    """

    def __init__(self) -> None:
        self._observers: list[tuple[RiskObserver, RiskEventType | None]] = []
        self._last_event: RiskEvent | None = None

    def attach(
        self,
        observer: RiskObserver,
        event_type: RiskEventType | None = None,
    ) -> None:
        """Register an observer. If *event_type* is None, it receives all events."""
        self._observers.append((observer, event_type))
        log.info(
            "Attached %s (filter=%s)",
            observer.__class__.__name__,
            event_type or "ALL",
        )

    def detach(self, observer: RiskObserver) -> None:
        self._observers = [
            (obs, et) for obs, et in self._observers if obs is not observer
        ]

    def notify_observers(self) -> None:
        """Dispatch the most recent event to all matching observers."""
        if self._last_event is None:
            return
        self._dispatch(self._last_event)

    def notify(self, event: RiskEvent) -> None:
        """Store *event* and dispatch to all matching observers."""
        self._last_event = event
        self._dispatch(event)

    def set_price(self, symbol: str, price: float) -> None:
        """Broadcast a price update to all observers (mirrors Stock.set_price in the diagram)."""
        for observer, _filter_type in self._observers:
            try:
                observer.update(symbol, price)
            except Exception:
                log.exception(
                    "Observer %s failed on update(%s, %s)",
                    observer.__class__.__name__, symbol, price,
                )

    def _dispatch(self, event: RiskEvent) -> None:
        for observer, filter_type in self._observers:
            if filter_type is not None and filter_type != event.event_type:
                continue
            try:
                observer.on_risk_event(event)
            except Exception:
                log.exception(
                    "Observer %s failed on %s",
                    observer.__class__.__name__,
                    event.event_type,
                )
