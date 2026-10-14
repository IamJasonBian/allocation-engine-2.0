"""Per-symbol equity trailing-stop percentages and lot coverage."""

EQUITY_STOP_MAPPING: dict[str, dict[str, float]] = {
    "MU": {"trail": 4.0, "qty_fraction": 0.25},
    "NBIS": {"trail": 6.0, "qty_fraction": 0.25},
}


def _spec(symbol: str) -> dict[str, float] | None:
    return EQUITY_STOP_MAPPING.get(symbol.upper())


def equity_stop_percent(symbol: str, default_percent: float) -> float:
    """Return the symbol override or the configured portfolio default."""
    spec = _spec(symbol)
    if spec is None:
        return float(default_percent)
    return float(spec["trail"])


def equity_stop_qty_fraction(symbol: str, default_fraction: float = 1.0) -> float:
    """Return the mapped lot fraction, or the full lot for unmapped names."""
    spec = _spec(symbol)
    if spec is None:
        return float(default_fraction)
    return float(spec.get("qty_fraction", default_fraction))
