"""Per-symbol equity trailing-stop percentages."""

EQUITY_STOP_MAPPING: dict[str, float] = {
    "MU": 4.0,
    "NBIS": 6.0,
}


def equity_stop_percent(symbol: str, default_percent: float) -> float:
    """Return the symbol override or the configured portfolio default."""
    return EQUITY_STOP_MAPPING.get(symbol.upper(), float(default_percent))
