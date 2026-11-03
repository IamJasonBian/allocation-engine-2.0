"""Registry and worker tick runner for option alert rulesets."""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Protocol

from app.risk.events import RiskEvent
from app.risk.option_alert_rulesets.declared import ALL


class RegistryError(ValueError):
    """Declared rulesets and OPTION_ALERT_RULESETS_ENABLED disagree."""


class OptionAlertRuleset(Protocol):
    code: str

    def initial_state(self) -> Any: ...

    def tick(
        self,
        options_positions: list[dict],
        state: Any,
        *,
        today: date | None = None,
    ) -> tuple[list[RiskEvent], Any]: ...


Registry = dict[str, OptionAlertRuleset]


def parse_enabled() -> frozenset[str]:
    raw = os.getenv("OPTION_ALERT_RULESETS_ENABLED", "drawdown,mental_stop")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def build_registry(
    enabled: frozenset[str] | None = None,
    declared: tuple[OptionAlertRuleset, ...] = ALL,
) -> Registry:
    """Return enabled rulesets by code, in sorted code order."""
    by_code: dict[str, OptionAlertRuleset] = {}
    for ruleset in declared:
        if ruleset.code in by_code:
            raise RegistryError(f"duplicate ruleset code {ruleset.code!r}")
        by_code[ruleset.code] = ruleset
    enabled = enabled if enabled is not None else parse_enabled()
    unknown = sorted(enabled - by_code.keys())
    if unknown:
        raise RegistryError(
            f"OPTION_ALERT_RULESETS_ENABLED names unknown rulesets: {unknown}; "
            f"declared: {sorted(by_code)}",
        )
    return {code: by_code[code] for code in sorted(enabled)}


def run_tick(
    options_positions: list[dict],
    states: dict[str, Any] | None,
    registry: Registry | None = None,
    *,
    today: date | None = None,
) -> tuple[list[RiskEvent], dict[str, Any]]:
    """Run every enabled ruleset once; merge events and per-ruleset state."""
    registry = registry or build_registry()
    states = dict(states or {})
    events: list[RiskEvent] = []
    for code, ruleset in registry.items():
        prior = states.get(code, ruleset.initial_state())
        new_events, new_state = ruleset.tick(options_positions, prior, today=today)
        events.extend(new_events)
        states[code] = new_state
    return events, states
