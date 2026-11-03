#!/usr/bin/env python3
"""Replay the spread-manager over a fake timeline. No network, no auth.

    python scripts/simulate_spread_manager.py            # fill -> TP -> guard -> exit
    python scripts/simulate_spread_manager.py --gap-up   # AMD gaps up, TP fills
"""

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.spread_manager import Config, SpreadSpec, step  # noqa: E402

ET = ZoneInfo("America/New_York")
SPEC = SpreadSpec("ord-1", "L", "S", "AMD", date(2026, 11, 4), 470.0, 510.0)


class Sim:
    """In-memory broker: one opening order, at most one closing order."""

    def __init__(self):
        self.state, self.close, self.qty, self.bid, self.n = "queued", None, 0, 20.0, 0

    def _open(self):
        return {"id": "ord-1", "state": self.state, "direction": "debit",
                "processed_quantity": "1" if self.state == "filled" else "0",
                "processed_premium": "1280" if self.state == "filled" else "0",
                "legs": [{"option": "x/L/", "side": "buy", "position_effect": "open"},
                         {"option": "x/S/", "side": "sell", "position_effect": "open"}]}

    def order(self, oid):
        if oid == "ord-1":
            return self._open()
        return {"id": oid, "state": self.close["state"], "price": self.close["price"]}

    def open_close_orders(self, _):
        c = self.close
        return [{"id": c["id"], "price": c["price"]}] if c and c["state"] == "confirmed" else []

    def open_qty(self, _): return self.qty
    def spread_quote(self, _): return self.bid, self.bid + 0.4

    def place_close(self, _s, qty, credit):
        self.n += 1
        self.close = {"id": f"close-{self.n}", "state": "confirmed", "price": str(credit)}
        return self.close["id"]

    def cancel_close(self, _s, oid): self.close["state"] = "cancelled"


def main(argv=None) -> list[str]:
    gap_up = "--gap-up" in (argv if argv is not None else sys.argv[1:])
    sim, memo, rows = Sim(), {}, []
    cfg = Config(dry_run=False, tp_not_before=datetime(2026, 11, 4, 9, 30, tzinfo=ET))
    t = lambda d, h, m: datetime(2026, 11, d, h, m, tzinfo=ET)
    timeline = [
        (t(2, 22, 0), "Mon night: order queued", None),
        (t(3, 9, 35), "Tue open: order fills @12.80", lambda: setattr(sim, "state", "filled") or setattr(sim, "qty", 1)),
        (t(3, 9, 36), "Tue: hold through the report", None),
        (t(3, 16, 5), "Tue after close (AMD reports)", None),
        (t(4, 9, 31), "Wed open: take-profit rests", (lambda: setattr(sim, "bid", 20.0)) if not gap_up else
            (lambda: sim.close.update(state="filled") or setattr(sim, "qty", 0))),
        (t(4, 9, 46), "Wed 9:45 guard window", lambda: setattr(sim, "bid", 21.0)),
        (t(4, 9, 47), "Wed guard re-check (no duplicate)", None),
        (t(4, 12, 31), "Wed 12:30 hard window", lambda: setattr(sim, "bid", 2.0)),
        (t(4, 12, 32), "Wed hard re-check", None),
    ]
    for when, label, action in timeline:
        if action:
            action()
        out = step(sim, SPEC, cfg, when, memo)
        rows.append(f"{when:%a %H:%M}  {label:<38} -> {out}")
        if out.startswith("done:"):
            break
    print("\n".join(rows))
    return rows


if __name__ == "__main__":
    main()
