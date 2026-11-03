"""Ruleset instances — add new alerts here (validation_service-style)."""

from app.risk.option_alert_rulesets.drawdown import DrawdownRuleset
from app.risk.option_alert_rulesets.mental_stop import MentalStopRuleset

ALL: tuple[DrawdownRuleset | MentalStopRuleset, ...] = (
    DrawdownRuleset(),
    MentalStopRuleset(),
)
