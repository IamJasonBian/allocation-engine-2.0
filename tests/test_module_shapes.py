"""Smoke tests for the risk-metrics and earnings shapes.

These are contracts, not implementations — the tests pin the shape so a later
fill-in cannot quietly change it, and assert the abstract methods stay abstract.
"""

import pytest

from app.earnings import EarningsEvent, EarningsEventType, EarningsFeed, EarningsMonitor
from app.earnings.feed import RECORD_FIELDS
from app.risk import MetricsCalculator, PortfolioMetrics, PositionMetric


def test_portfolio_metrics_defaults_are_an_empty_book():
    m = PortfolioMetrics()
    assert m.positions == [] and m.position_count == 0
    assert m.leverage == 0.0, "leverage must not divide by zero equity"
    assert (m.realized_vol, m.var_95) == (None, None)


def test_leverage_is_gross_over_equity():
    assert PortfolioMetrics(gross_exposure=150.0, equity=100.0).leverage == 1.5


def test_position_metric_carries_weight_and_optional_vol():
    p = PositionMetric(symbol="NVDA", quantity=10, market_value=1000.0, weight=0.25)
    assert p.weight == 0.25 and p.sigma is None


def test_earnings_surprise_is_dollars_and_none_when_incomplete():
    released = EarningsEvent(EarningsEventType.REPORT_RELEASED, "AVGO",
                             "2026-09-02", "printed", eps_estimate=3.24,
                             eps_actual=3.30)
    assert released.surprise == pytest.approx(0.06)

    scheduled = EarningsEvent(EarningsEventType.REPORT_SCHEDULED, "AVGO",
                              "2026-09-02", "scheduled", eps_estimate=3.24)
    assert scheduled.surprise is None


def test_feed_record_fields_match_the_writer_contract():
    from app.earnings_writer import RECORD_FIELDS as writer_fields
    assert RECORD_FIELDS == writer_fields


def test_abstract_shapes_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MetricsCalculator()
    with pytest.raises(TypeError):
        EarningsMonitor()


def test_earnings_feed_is_a_structural_protocol():
    class Stub:
        name = "stub"

        def fetch(self, tickers, quarters=12, as_of=None):
            return [], []

    assert isinstance(Stub(), EarningsFeed)
