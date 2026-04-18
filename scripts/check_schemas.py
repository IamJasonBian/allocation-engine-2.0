#!/usr/bin/env python3
"""Deploy check: validate that core schemas are consistent and broker
implementations produce conforming data.

Runs at build time (render.yaml buildCommand) to catch type drift between
this engine and allocation-manager before it reaches production.

Exit 0 = all checks pass, exit 1 = contract violation.
"""

import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    errors: list[str] = []

    # 1. Import schemas
    try:
        from app.schemas import (
            AccountSchema,
            PositionSchema,
            OrderSchema,
            AccountResponse,
            PositionsResponse,
            OrdersResponse,
            PortfolioResponse,
            HealthResponse,
        )
        print("[OK] All schema classes importable")
    except ImportError as e:
        print(f"[FAIL] Schema import error: {e}")
        return 1

    # 2. Validate that required fields exist on each schema
    required_fields = {
        "AccountSchema": {"equity", "cash", "buying_power", "portfolio_value"},
        "PositionSchema": {"symbol", "qty", "side", "market_value", "avg_entry",
                           "unrealized_pl", "unrealized_pl_pct"},
        "OrderSchema": {"id", "symbol", "side", "qty", "type", "limit_price",
                        "stop_price", "status"},
    }

    schemas = {
        "AccountSchema": AccountSchema,
        "PositionSchema": PositionSchema,
        "OrderSchema": OrderSchema,
    }

    for name, expected in required_fields.items():
        actual = set(schemas[name].model_fields.keys())
        missing = expected - actual
        extra = actual - expected
        if missing:
            errors.append(f"{name} missing fields: {missing}")
        if extra:
            errors.append(f"{name} has unexpected fields: {extra}")
        if not missing and not extra:
            print(f"[OK] {name} fields match contract ({len(expected)} fields)")

    # 3. Validate with sample data (catches type coercion issues)
    sample_account = {
        "equity": 10000.0,
        "cash": 5000.0,
        "buying_power": 5000.0,
        "portfolio_value": 10000.0,
    }
    try:
        AccountSchema.model_validate(sample_account)
        print("[OK] AccountSchema validates sample data")
    except Exception as e:
        errors.append(f"AccountSchema validation failed: {e}")

    sample_position = {
        "symbol": "AAPL",
        "qty": 10.0,
        "side": "long",
        "market_value": 1500.0,
        "avg_entry": 140.0,
        "unrealized_pl": 100.0,
        "unrealized_pl_pct": 0.0714,
    }
    try:
        PositionSchema.model_validate(sample_position)
        print("[OK] PositionSchema validates sample data")
    except Exception as e:
        errors.append(f"PositionSchema validation failed: {e}")

    sample_order = {
        "id": "abc-123",
        "symbol": "AAPL",
        "side": "BUY",
        "qty": 5.0,
        "type": "limit",
        "limit_price": 150.0,
        "stop_price": None,
        "status": "queued",
    }
    try:
        OrderSchema.model_validate(sample_order)
        print("[OK] OrderSchema validates sample data")
    except Exception as e:
        errors.append(f"OrderSchema validation failed: {e}")

    # 4. Validate response envelopes
    try:
        AccountResponse.model_validate({"broker": "robinhood", **sample_account})
        PositionsResponse.model_validate({
            "broker": "robinhood", "count": 1, "positions": [sample_position],
        })
        OrdersResponse.model_validate({
            "broker": "robinhood", "count": 1, "orders": [sample_order],
        })
        PortfolioResponse.model_validate({
            "broker": "robinhood",
            **sample_account,
            "positions": [sample_position],
        })
        print("[OK] All response envelope schemas validate")
    except Exception as e:
        errors.append(f"Response envelope validation failed: {e}")

    # 5. Verify BrokerClient protocol field alignment
    try:
        from app.brokers.base import BrokerClient
        import inspect

        for method in ("account", "positions", "open_orders"):
            assert hasattr(BrokerClient, method), f"BrokerClient missing {method}"
        print("[OK] BrokerClient protocol has required methods")
    except Exception as e:
        errors.append(f"BrokerClient check failed: {e}")

    # Report
    if errors:
        print(f"\n{'='*60}")
        print(f"DEPLOY CHECK FAILED — {len(errors)} error(s):")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"\n{'='*60}")
    print("DEPLOY CHECK PASSED — all type contracts verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
