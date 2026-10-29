#!/usr/bin/env python3
"""Pull Trading DB orders/positions and print option prev_iv marks."""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import date, timedelta

from app.pricing.black_scholes import occ_symbol
from app.pricing.mark_models import mark_call_at_spot
from app.pricing.yahoo_closes import fetch_daily_closes

DEFAULT_BASE = "https://5thstreetcapital.org/.netlify/functions"


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "allocation-engine-mark/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def equity_spot(positions_payload: dict, symbol: str) -> float | None:
    for row in positions_payload.get("data", {}).get("positions", []):
        if row.get("symbol") == symbol:
            return float(row["current_price"])
    return None


def held_calls_from_orders(orders_payload: dict) -> list[dict]:
    """Infer open long calls from recent fills (net qty by contract)."""
    lots: dict[tuple, float] = {}
    for bucket in ("historical_option_orders", "open_option_orders"):
        for row in orders_payload.get("data", {}).get(bucket, []):
            if row.get("state") != "filled":
                continue
            for leg in row.get("legs") or []:
                if (leg.get("option_type") or "").lower() != "call":
                    continue
                if (leg.get("side") or "").upper() != "BUY":
                    continue
                key = (
                    leg.get("chain_symbol"),
                    float(leg.get("strike_price")),
                    leg.get("expiration_date"),
                )
                qty = float(row.get("quantity") or 0)
                lots[key] = lots.get(key, 0.0) + qty
    out = []
    for (sym, strike, expiry), qty in sorted(lots.items()):
        if qty > 0.01:
            out.append({"symbol": sym, "strike": strike, "expiry": expiry, "qty": qty})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument(
        "--as-of",
        default=None,
        help="mark date (default: day after latest Yahoo option close)",
    )
    ap.add_argument("--spot", type=float, help="override NBIS spot (default: from positions)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    orders = fetch_json(f"{args.base}/db-orders?limit=120")
    positions = fetch_json(f"{args.base}/db-positions")
    spot = args.spot or equity_spot(positions, "NBIS")
    if spot is None:
        raise SystemExit("NBIS spot not found in positions payload")

    marks = []
    for lot in held_calls_from_orders(orders):
        if lot["symbol"] != "NBIS":
            continue
        u = fetch_daily_closes("NBIS")
        occ = occ_symbol(lot["symbol"], lot["expiry"], lot["strike"])
        o = fetch_daily_closes(occ)
        shared = sorted(d for d in o if d in u)
        if len(shared) < 2:
            continue
        last_close = shared[-1]
        as_of = args.as_of
        if not as_of:
            as_of = (date.fromisoformat(last_close) + timedelta(days=1)).isoformat()
        mark = mark_call_at_spot(
            spot,
            strike=lot["strike"],
            expiry=lot["expiry"],
            as_of=as_of,
            underlying=u,
            option=o,
        )
        mark["qty"] = lot["qty"]
        mark["occ"] = occ
        marks.append(mark)

    mark_as_of = marks[0]["as_of"] if marks else (args.as_of or date.today().isoformat())
    payload = {
        "as_of": mark_as_of,
        "orders_as_of": orders.get("as_of"),
        "positions_as_of": positions.get("as_of"),
        "nbis_spot": spot,
        "marks": marks,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"NBIS spot ${spot:.2f}  as_of={mark_as_of}")
        for m in marks:
            print(
                f"  {m['qty']:g}x {m['expiry']} ${m['strike']:.0f}C  "
                f"prev_iv=${m['marks']['prev_iv']:.2f}  "
                f"entry_iv=${m['marks']['entry_iv']:.2f}  "
                f"rv=${m['marks']['realized']:.2f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
