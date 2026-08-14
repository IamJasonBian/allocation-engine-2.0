"""Portfolio risk metrics — SHAPE ONLY, no implementation yet.

The rest of `app/risk/` is event plumbing: something notices a condition and
publishes it. This is the other half — run against the *existing* book on a
tick and produce the numbers those events would fire on.

Nothing here calculates yet. The dataclasses fix the output contract so the
dashboard and the event thresholds can be written against it, and
`MetricsCalculator.calculate` marks where the math lands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class PositionMetric:
    """Per-position risk contribution."""

    symbol: str
    quantity: float
    market_value: float
    weight: float                      # fraction of gross exposure, 0-1
    unrealized_pl: float | None = None
    unrealized_pl_pct: float | None = None
    # TODO: per-position vol / beta / stop distance once a sigma source exists
    # (see docs/TRAILING_STOP_WATERFALL.md — no sigma source is wired today).
    sigma: float | None = None
    beta: float | None = None


@dataclass(frozen=True)
class PortfolioMetrics:
    """Book-level snapshot for one tick."""

    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    positions: list[PositionMetric] = field(default_factory=list)

    gross_exposure: float = 0.0        # sum of |market_value|
    net_exposure: float = 0.0          # sum of market_value, signed
    cash: float = 0.0
    equity: float = 0.0

    # Concentration: the largest single weight, and how much the top N carry.
    max_weight: float = 0.0
    top_n_weight: float = 0.0
    position_count: int = 0

    # TODO: needs a price history source — none is wired (Redis holds ~8h).
    realized_vol: float | None = None
    var_95: float | None = None
    max_drawdown: float | None = None

    @property
    def leverage(self) -> float:
        """Gross exposure over equity; 0 when equity is unknown."""
        return self.gross_exposure / self.equity if self.equity else 0.0


class MetricsCalculator(ABC):
    """Computes `PortfolioMetrics` from a broker's current book."""

    @abstractmethod
    def calculate(
        self,
        positions: list[dict],
        account: dict,
        prices: dict[str, float] | None = None,
    ) -> PortfolioMetrics:
        """Build a metrics snapshot for one tick.

        Args:
            positions: broker positions (symbol, qty, market_value, avg_entry).
            account: broker account summary (equity, cash, buying_power).
            prices: symbol -> live price, when fresher than the broker's marks.

        Returns:
            The snapshot.
        """
        raise NotImplementedError


# TODO(risk-metrics): wire a concrete calculator into the engine tick.
#   - weights off market_value, not quantity — 50 shares of two names is not
#     two equal positions (the same mistake the qty-only order cap made)
#   - emit POSITION_LIMIT when max_weight crosses a configured threshold;
#     the RiskSubject bus and the enum member already exist
#   - vol/VaR/drawdown stay None until a price-history source lands
