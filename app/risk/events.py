"""Risk event data model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.enums import RiskEventType


@dataclass(frozen=True)
class RiskEvent:
    """Immutable risk event emitted by the engine during reconciliation."""

    event_type: RiskEventType
    symbol: str
    drift_pct: float                          # observed drift as a decimal (0.09 = 9%)
    message: str
    snapshot_key: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def severity(self) -> str:
        if self.drift_pct >= 0.15:
            return "critical"
        if self.drift_pct >= 0.08:
            return "warning"
        return "info"
