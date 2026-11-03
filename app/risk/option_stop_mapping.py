"""Per-underlying option stop-loss percentages.

Keys are chain symbols (``"MU"``); values are the premium drawdown, in percent
of the RH lot purchase price, at which the protective stop triggers. Anything
not listed uses Config.OPTION_SL_PERCENT.
"""

OPTION_STOP_MAPPING: dict[str, float] = {}


def option_stop_percent(chain_symbol: str, default: float) -> float:
    """Stop-loss percent for an underlying, falling back to the default.

    Args:
        chain_symbol: Option chain symbol, any case.
        default: Percent used when the symbol has no override.

    Returns:
        Premium drawdown percent at which the stop triggers.
    """
    return float(OPTION_STOP_MAPPING.get(str(chain_symbol).upper(), default))
