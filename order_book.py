"""In-memory order book and coverage audit.

Tracks the lifecycle of orders submitted to Alpaca (submitted → filled/cancelled)
and audits position protection coverage.
"""

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class OrderBook:
    """Tracks orders submitted by this engine session."""

    def __init__(self):
        self.orders: dict[str, dict] = {}  # alpaca_id → order record
        self.fills: list[dict] = []
        self.cancels: list[dict] = []

    def record_submit(self, alpaca_id: str, symbol: str, side: str,
                      qty: float, limit_price: float | None,
                      order_type: str = "simple", oto_buy_price: float | None = None):
        self.orders[alpaca_id] = {
            "id": alpaca_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "limit_price": limit_price,
            "order_type": order_type,
            "oto_buy_price": oto_buy_price,
            "status": "submitted",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def sync_with_alpaca(self, alpaca_orders: list[dict]):
        """Update internal state from Alpaca's order list.

        Detects fills (orders we tracked that are no longer open) and
        status changes.
        """
        open_ids = {o["id"] for o in alpaca_orders}

        for aid, rec in list(self.orders.items()):
            if rec["status"] == "submitted" and aid not in open_ids:
                rec["status"] = "filled_or_cancelled"
                rec["resolved_at"] = datetime.now(timezone.utc).isoformat()
                self.fills.append(rec)
                log.info("Order resolved: %s %s %s @ %s",
                         rec["side"], rec["qty"], rec["symbol"], rec["limit_price"])

        # Also track any Alpaca orders we didn't submit (manual, OTO legs)
        for o in alpaca_orders:
            if o["id"] not in self.orders:
                self.orders[o["id"]] = {
                    "id": o["id"],
                    "symbol": o["symbol"],
                    "side": o["side"],
                    "qty": o["qty"],
                    "limit_price": o["limit_price"],
                    "order_type": "external",
                    "status": o["status"],
                    "submitted_at": None,
                }

    def summary(self) -> dict:
        submitted = [o for o in self.orders.values() if o["status"] == "submitted"]
        resolved = [o for o in self.orders.values() if o["status"] == "filled_or_cancelled"]
        return {
            "total_tracked": len(self.orders),
            "open": len(submitted),
            "resolved": len(resolved),
            "fills_this_session": len(self.fills),
        }


def audit_coverage(positions: list[dict], orders: list[dict]) -> dict:
    """Audit sell-side order coverage against open positions.

    For each position, checks what percentage of shares are covered
    by SELL orders (limit, stop, OTO legs).
    """
    if not positions:
        return {
            "total_positions": 0,
            "total_equity": 0.0,
            "covered_equity": 0.0,
            "coverage_pct": 0.0,
            "details": [],
            "uncovered": [],
        }

    total_equity = sum(p.get("market_value", 0) for p in positions)
    covered_equity = 0.0
    details = []
    uncovered = []

    for pos in positions:
        sym = pos["symbol"]
        pos_qty = abs(float(pos.get("qty", 0)))
        pos_equity = float(pos.get("market_value", 0))

        # Find all SELL orders for this symbol
        sell_orders = [o for o in orders
                       if o["symbol"] == sym and o["side"] == "sell"]
        sell_qty = sum(float(o.get("qty", 0) or 0) for o in sell_orders)
        coverage = min(sell_qty / pos_qty, 1.0) if pos_qty > 0 else 0.0

        detail = {
            "symbol": sym,
            "position_qty": pos_qty,
            "position_equity": pos_equity,
            "sell_order_qty": sell_qty,
            "coverage_pct": coverage * 100,
            "sell_orders": [
                {
                    "id": o["id"][:8],
                    "qty": o["qty"],
                    "type": o["type"],
                    "limit": o.get("limit_price"),
                    "stop": o.get("stop_price"),
                }
                for o in sell_orders
            ],
        }
        details.append(detail)

        if coverage > 0:
            covered_equity += pos_equity * coverage
        else:
            uncovered.append(detail)

    details.sort(key=lambda d: d["position_equity"], reverse=True)

    return {
        "total_positions": len(positions),
        "total_equity": total_equity,
        "covered_equity": covered_equity,
        "coverage_pct": (covered_equity / total_equity * 100) if total_equity > 0 else 0.0,
        "details": details,
        "uncovered": uncovered,
    }


def print_audit(report: dict):
    """Print formatted audit report."""
    print(f"\n{'='*60}")
    print("ORDER COVERAGE AUDIT")
    print(f"{'='*60}")
    print(f"  Positions: {report['total_positions']}")
    print(f"  Total Equity: ${report['total_equity']:,.2f}")
    print(f"  Covered Equity: ${report['covered_equity']:,.2f} ({report['coverage_pct']:.1f}%)")

    if not report["details"]:
        print("  No positions to audit")
        print(f"{'='*60}\n")
        return

    print(f"\n  Per-Position Breakdown:")
    for d in report["details"]:
        icon = "OK" if d["coverage_pct"] >= 100 else ("PARTIAL" if d["coverage_pct"] > 0 else "NONE")
        print(f"    [{icon:>7}] {d['symbol']:>6}: {d['position_qty']:.0f} shares, "
              f"${d['position_equity']:,.2f}, {d['coverage_pct']:.0f}% covered")
        for so in d["sell_orders"]:
            price_str = f"limit={so['limit']}" if so["limit"] else f"stop={so['stop']}"
            print(f"             {so['id']}  {so['type']} {so['qty']} {price_str}")

    if report["uncovered"]:
        print(f"\n  ALERT: {len(report['uncovered'])} uncovered position(s):")
        for u in report["uncovered"]:
            print(f"    {u['symbol']}: ${u['position_equity']:,.2f}")

    print(f"{'='*60}\n")
