#!/usr/bin/env python3
"""Mark a held call with realized / entry_iv / prev_iv models.

Example (NBIS 6× Nov-20 $230C, overnight spot $164):

    PYTHONPATH=. python3 scripts/option_mark.py \\
        --spot 164 --as-of 2026-10-21
"""

from __future__ import annotations

import argparse
import json
import sys

from app.pricing.black_scholes import occ_symbol
from app.pricing.mark_models import mark_call_at_spot
from app.pricing.yahoo_closes import fetch_daily_closes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--symbol", default="NBIS")
    ap.add_argument("--strike", type=float, default=230.0)
    ap.add_argument("--expiry", default="2026-11-20")
    ap.add_argument("--spot", type=float, required=True)
    ap.add_argument("--as-of", default="2026-10-21", help="mark date (ISO); history must exist before this")
    ap.add_argument("--start", default="2026-09-29", help="first day for entry_iv seed")
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    args = ap.parse_args(argv)

    contract = occ_symbol(args.symbol, args.expiry, args.strike)
    underlying = fetch_daily_closes(args.symbol)
    option = fetch_daily_closes(contract)
    mark = mark_call_at_spot(
        args.spot,
        strike=args.strike,
        expiry=args.expiry,
        as_of=args.as_of,
        underlying=underlying,
        option=option,
        start=args.start,
    )
    mark["symbol"] = args.symbol
    mark["occ"] = contract

    if args.json:
        print(json.dumps(mark, indent=2))
        return 0

    print(f"{contract}  spot=${args.spot:.2f}  as_of={args.as_of}  dte={mark['dte']}")
    print(
        f"prev {mark['prev_day']}: spot ${mark['prev_spot']:.2f}  "
        f"close ${mark['prev_option_close']:.2f}  iv={mark['prev_iv']:.1%}"
    )
    for name in ("prev_iv", "entry_iv", "realized"):
        px = mark["marks"][name]
        print(f"  {name:9}  ${px:.2f}" if px is not None else f"  {name:9}  —")
    return 0


if __name__ == "__main__":
    sys.exit(main())
