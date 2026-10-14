from app.risk.equity_stop_mapping import (
    EQUITY_STOP_MAPPING,
    equity_stop_percent,
    equity_stop_qty_fraction,
)
from app.risk.option_stop_mapping import OPTION_STOP_MAPPING


def test_equity_stop_overrides_and_default():
    assert EQUITY_STOP_MAPPING == {
        "MU": {"trail": 4.0, "qty_fraction": 0.25},
        "NBIS": {"trail": 6.0, "qty_fraction": 0.25},
    }
    assert equity_stop_percent("mu", 12) == 4.0
    assert equity_stop_percent("NBIS", 12) == 6.0
    assert equity_stop_percent("AAPL", 12) == 12.0
    assert equity_stop_qty_fraction("MU") == 0.25
    assert equity_stop_qty_fraction("nbis") == 0.25
    assert equity_stop_qty_fraction("AAPL") == 1.0


def test_option_stop_mapping_is_todo_placeholder():
    assert OPTION_STOP_MAPPING == {}
