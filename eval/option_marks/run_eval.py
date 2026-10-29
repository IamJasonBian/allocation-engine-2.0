#!/usr/bin/env python3
"""Score eval/option_marks/cases.jsonl against pricing models."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pricing.black_scholes import occ_symbol
from app.pricing.mark_models import MODELS, mark_call_at_spot
from app.pricing.yahoo_closes import fetch_daily_closes
from app.pubsub.bounce import RECIPE
from app.pubsub.option_pricer import resolve_long_call

CASES = Path(__file__).parent / "cases.jsonl"
SCORED = Path(__file__).parent / "scored.jsonl"
DEFAULT_BASE = "https://5thstreetcapital.org/.netlify/functions"


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "allocation-engine-eval/1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _equity_spot(symbol: str, base: str) -> float:
    positions = _fetch_json(f"{base}/db-positions")
    for row in positions.get("data", {}).get("positions", []):
        if row.get("symbol") == symbol:
            return float(row["current_price"])
    raise ValueError(f"{symbol} spot not in positions payload")


def _next_rth_as_of(option_closes: dict[str, float]) -> str:
    last = max(option_closes)
    return (date.fromisoformat(last) + timedelta(days=1)).isoformat()


def _in_band(value: float | None, band: list[float] | None) -> bool | None:
    if band is None:
        return None
    if value is None:
        return False
    lo, hi = band
    return lo <= value <= hi


def _run_held_mark(case: dict, *, live: bool, base: str) -> dict:
    symbol = case["symbol"]
    spot_spec = case["spot"]
    if isinstance(spot_spec, str) and spot_spec.startswith("live:"):
        if not live and not case.get("live"):
            return {"id": case["id"], "skipped": True, "reason": "use --live for live:* spot"}
        sym = spot_spec.split(":", 1)[1]
        spot = _equity_spot(sym, base)
    else:
        spot = float(spot_spec)

    underlying = fetch_daily_closes(symbol)
    occ = occ_symbol(symbol, case["expiry"], float(case["strike"]))
    option = fetch_daily_closes(occ)
    as_of = case["as_of"]
    if as_of == "next_rth":
        as_of = _next_rth_as_of(option)

    mark = mark_call_at_spot(
        spot,
        strike=float(case["strike"]),
        expiry=case["expiry"],
        as_of=as_of,
        underlying=underlying,
        option=option,
    )
    mark["qty"] = case.get("qty")
    mark["case_id"] = case["id"]

    expect = case.get("expect_marks") or {}
    checks = {}
    ok = True
    for name in MODELS:
        band = expect.get(name)
        if band is None:
            continue
        passed = _in_band(mark["marks"].get(name), band)
        checks[name] = {"value": mark["marks"].get(name), "band": band, "ok": passed}
        if passed is False:
            ok = False

    return {"id": case["id"], "kind": "held_mark", "ok": ok, "mark": mark, "checks": checks}


def _run_resolve(case: dict) -> dict:
    chain_path = Path(__file__).parent / case["chain"]
    chain = json.loads(chain_path.read_text())
    recipe = case.get("recipe") or RECIPE
    ticket = resolve_long_call(chain, recipe)
    expect = case.get("expect") or {}
    ok = ticket is not None
    checks: dict = {"resolved": ticket is not None}
    if ticket and "strike_min" in expect:
        checks["strike"] = ticket["strike"]
        ok = ok and expect["strike_min"] <= ticket["strike"] <= expect["strike_max"]
    if ticket and "limit_max" in expect:
        checks["limit"] = ticket["limit"]
        ok = ok and ticket["limit"] <= expect["limit_max"]
    return {
        "id": case["id"],
        "kind": "resolve_long_call",
        "ok": ok,
        "ticket": ticket,
        "checks": checks,
        "chain_as_of": chain.get("as_of"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cases", type=Path, default=CASES)
    ap.add_argument("--out", type=Path, default=SCORED)
    ap.add_argument("--live", action="store_true", help="run cases with live:* spots")
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args(argv)

    rows: list[dict] = []
    passed = failed = skipped = 0

    for line in args.cases.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        case = json.loads(line)
        kind = case.get("kind", "held_mark")
        try:
            if kind == "held_mark":
                row = _run_held_mark(case, live=args.live, base=args.base)
            elif kind == "resolve_long_call":
                row = _run_resolve(case)
            else:
                row = {"id": case.get("id"), "ok": False, "error": f"unknown kind {kind}"}
        except Exception as exc:
            row = {"id": case.get("id"), "kind": kind, "ok": False, "error": str(exc)}

        rows.append(row)
        if row.get("skipped"):
            skipped += 1
        elif row.get("ok"):
            passed += 1
        else:
            failed += 1

    args.out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    print(f"cases={len(rows)} pass={passed} fail={failed} skip={skipped} → {args.out}", file=sys.stderr)
    for r in rows:
        if r.get("skipped"):
            print(f"  SKIP {r['id']}: {r.get('reason')}", file=sys.stderr)
        elif not r.get("ok"):
            print(f"  FAIL {r['id']}: {json.dumps(r.get('checks') or r.get('error'))}", file=sys.stderr)
        else:
            if r.get("kind") == "held_mark":
                m = r["mark"]
                print(
                    f"  OK   {r['id']}: {m['occ']} spot=${m['spot']:.2f} "
                    f"prev_iv=${m['marks']['prev_iv']:.2f}",
                    file=sys.stderr,
                )
            else:
                t = r.get("ticket") or {}
                print(
                    f"  OK   {r['id']}: {t.get('symbol')} {t.get('expiry')} "
                    f"${t.get('strike')}C limit=${t.get('limit')}",
                    file=sys.stderr,
                )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
