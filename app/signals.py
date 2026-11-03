"""Buy/sell signals from external systems, run through a chain of handlers.

We are a thin wrapper: the client owns the trade idea, we pass it through.
Every signal carries a caller-supplied GUID (``signal_id``) that every handler
and notification sees.

    dedupe     — a repeated signal_id returns the original result (24h)
    freshness  — past expires_at (default received + 5 min) → marked expired
    cooldown   — same side+symbol within 5 min → suppressed, counted
    <listeners>— SIGNAL_HANDLERS, in order (today: "alert")

The first three always run; listeners are configurable. A handler may stop
the chain (duplicate, suppressed); later handlers then don't run.
"""

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from app.notifier import notify

SIDES = ("BUY", "SELL")
MARKET_HOURS = ("regular_hours", "extended_hours", "all_day_hours")


@dataclass(frozen=True)
class Signal:
    signal_id: str
    side: str
    symbol: str
    quantity: float
    source: str
    received_at: datetime
    expires_at: datetime
    reason: str = ""
    limit_price: float | None = None
    market_hours: str = "regular_hours"
    authorized: bool = False


@dataclass
class Context:
    """What flows down the chain: the signal plus the result being built."""
    signal: Signal
    result: dict = field(default_factory=dict)
    expired: bool = False
    repeats_suppressed: int = 0
    stopped: bool = False


class Handler(Protocol):
    name: str

    def handle(self, ctx: Context) -> None: ...


class SignalMemory:
    """In-memory state shared by dedupe and cooldown (one API process).

    Lost on deploy by design: there is no store behind signals.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._seen: dict[str, tuple[float, dict]] = {}
        self._cool: dict[tuple[str, str], list] = {}  # key -> [last_ts, suppressed]

    def lookup(self, signal_id: str, ttl: float) -> dict | None:
        now = time.time()
        with self._lock:
            self._seen = {k: v for k, v in self._seen.items() if now - v[0] < ttl}
            hit = self._seen.get(signal_id)
            return dict(hit[1]) if hit else None

    def remember(self, signal_id: str, result: dict) -> None:
        with self._lock:
            self._seen[signal_id] = (time.time(), dict(result))

    def cool(self, key: tuple[str, str], window: float) -> tuple[bool, int]:
        """Record a hit on key; return (suppressed, repeats_to_report)."""
        now = time.time()
        with self._lock:
            slot = self._cool.get(key)
            if slot and now - slot[0] < window:
                slot[1] += 1
                return True, slot[1]
            repeats = slot[1] if slot else 0
            self._cool[key] = [now, 0]
            return False, repeats

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()
            self._cool.clear()


MEMORY = SignalMemory()


def describe(s: Signal) -> str:
    """One-line human summary of a signal, GUID included."""
    price = f" @ {s.limit_price}" if s.limit_price is not None else " @ market"
    hours = "" if s.market_hours == "regular_hours" else f" ({s.market_hours})"
    auth = "authorized" if s.authorized else "UNAUTHORIZED"
    why = f" — {s.reason}" if s.reason else ""
    return (f"{s.side} {s.quantity:g} {s.symbol}{price}{hours} from {s.source} "
            f"[{auth}]{why} · id={s.signal_id}")


class DedupeHandler:
    name = "dedupe"

    def __init__(self, memory: SignalMemory, ttl: float):
        self.memory, self.ttl = memory, ttl

    def handle(self, ctx: Context) -> None:
        prior = self.memory.lookup(ctx.signal.signal_id, self.ttl)
        if prior is not None:
            ctx.result = {**prior, "duplicate": True}
            ctx.stopped = True


class FreshnessHandler:
    name = "freshness"

    def handle(self, ctx: Context) -> None:
        if ctx.signal.received_at > ctx.signal.expires_at:
            ctx.expired = True
            ctx.result["status"] = "expired"


class CooldownHandler:
    name = "cooldown"

    def __init__(self, memory: SignalMemory, window: float):
        self.memory, self.window = memory, window

    def handle(self, ctx: Context) -> None:
        if ctx.expired:  # an expired signal shouldn't block a fresh one
            return
        suppressed, repeats = self.memory.cool((ctx.signal.side, ctx.signal.symbol), self.window)
        if suppressed:
            ctx.result.update(status="suppressed", repeats_in_window=repeats)
            ctx.stopped = True
        else:
            ctx.repeats_suppressed = repeats


class AlertHandler:
    name = "alert"

    def handle(self, ctx: Context) -> None:
        s = ctx.signal
        note = " → EXPIRED, notify only" if ctx.expired else " → notify only"
        if ctx.repeats_suppressed:
            note += f" (+{ctx.repeats_suppressed} repeats suppressed)"
        notify(f"[{s.side.lower()}-signal] {describe(s)}{note}")
        ctx.result.setdefault("status", "alerted")
        ctx.result["alerted"] = True


LISTENERS = {"alert": AlertHandler}


def build_chain(listener_names: list[str], memory: SignalMemory,
                dedupe_ttl: float, cooldown: float) -> list[Handler]:
    """Assemble the handler chain.

    Args:
        listener_names: SIGNAL_HANDLERS entries, in order (e.g. ["alert"]).
        memory: Shared dedupe/cooldown state.
        dedupe_ttl: Seconds a signal_id is remembered.
        cooldown: Seconds a side+symbol stays suppressed after a signal.

    Returns:
        Guards (dedupe, freshness, cooldown) followed by the listeners.

    Raises:
        ValueError: A listener name isn't registered.
    """
    unknown = [n for n in listener_names if n not in LISTENERS]
    if unknown:
        raise ValueError(f"unknown SIGNAL_HANDLERS: {unknown}; known: {sorted(LISTENERS)}")
    return [DedupeHandler(memory, dedupe_ttl), FreshnessHandler(),
            CooldownHandler(memory, cooldown)] + [LISTENERS[n]() for n in listener_names]


def run_chain(signal: Signal, chain: list[Handler], memory: SignalMemory) -> dict:
    """Run a signal through the chain and return its result.

    Args:
        signal: The incoming signal.
        chain: Handlers from build_chain.
        memory: Where the first result for this signal_id is remembered.

    Returns:
        The result dict, always including signal_id and the handlers that ran.
    """
    ctx = Context(signal=signal)
    ran = []
    for h in chain:
        h.handle(ctx)
        ran.append(h.name)
        if ctx.stopped:
            break
    if ctx.result.get("duplicate"):
        return ctx.result
    result = {"signal_id": signal.signal_id, "side": signal.side, "symbol": signal.symbol,
              "authorized": signal.authorized, "handlers": ran,
              "expires_at": signal.expires_at.isoformat(), **ctx.result}
    memory.remember(signal.signal_id, result)
    return result


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
