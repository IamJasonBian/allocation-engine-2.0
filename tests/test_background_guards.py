"""Guard around the Trading DB position sync.

post_positions is a whole-book replace, so publishing an empty book prunes
every row the dashboard renders. RobinhoodTrader.account() degrades a failed
profile fetch to zeros rather than raising, which makes a rejected session
look identical to a real but empty account — hence this guard.
"""

from app.background import book_looks_unreadable

ZERO_ACCOUNT = {"equity": 0.0, "cash": 0.0, "buying_power": 0.0,
                "portfolio_value": 0.0}
REAL_ACCOUNT = {"equity": 5000.0, "cash": 250.0, "buying_power": 500.0,
                "portfolio_value": 4750.0}
POSITION = {"symbol": "NVDA", "qty": 10}
OPTION = {"chain_symbol": "CRWD", "strike": 300}


def test_nothing_anywhere_is_treated_as_a_failed_read():
    # Exactly what prod returns today: authenticated, but every read empty.
    assert book_looks_unreadable([], [], ZERO_ACCOUNT) is True


def test_a_genuinely_flat_book_with_cash_still_publishes():
    # An emptied account keeps cash/buying power, so it clears normally.
    assert book_looks_unreadable([], [], {"equity": 0, "cash": 250.0,
                                          "buying_power": 0,
                                          "portfolio_value": 0}) is False


def test_a_normal_book_publishes():
    assert book_looks_unreadable([POSITION], [OPTION], REAL_ACCOUNT) is False

