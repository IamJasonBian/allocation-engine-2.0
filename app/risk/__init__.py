"""Risk package: the DQ event bus and the metrics & risk library.

Event infrastructure (Observer pattern for data-quality events, consumed by
`main.py`): `RiskEvent`, `RiskSubject`, `SlackAlertObserver`, `RebalancerObserver`.

Metrics & risk library — pure functions over the two inputs the engine already
produces (normalized fills, daily closes per symbol), organised by question:

- `curve`      — what did the book do each day? (mark-to-market equity curve)
- `metrics`    — how good was it? (return, vol, Sharpe, drawdown, hit rate)
- `exposure`   — what is the book right now? (gross/net, weights, concentration)
- `covariance` — how do the pieces move together? (Σ, ρ, true portfolio σ, MCTR)
- `tail`       — how bad is a bad day? (historical + parametric VaR / CVaR)
- `stress`     — what if? (shock scenarios and worst historical windows replayed)
- `report`     — everything above, assembled, with plain-English flags.

No numpy: the book is a handful of symbols, and the Cloud Run image stays lean.
"""

from app.risk.events import RiskEvent
from app.risk.observer import RiskObserver, Subject, RiskSubject
from app.risk.slack_observer import SlackAlertObserver
from app.risk.rebalancer_observer import RebalancerObserver
from app.risk.report import build_report

__all__ = [
    "RiskEvent",
    "RiskObserver",
    "Subject",
    "RiskSubject",
    "SlackAlertObserver",
    "RebalancerObserver",
    "build_report",
]
