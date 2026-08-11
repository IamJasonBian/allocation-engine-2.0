#!/usr/bin/env python3
"""Read-only IBKR connectivity probe for TWS or IB Gateway."""

import argparse
import json
import sys
from datetime import datetime, timezone

from ib_async import IB


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4002)
    parser.add_argument("--client-id", type=int, default=91)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    ib = IB()
    try:
        ib.connect(
            args.host,
            args.port,
            clientId=args.client_id,
            timeout=args.timeout,
            readonly=True,
            raiseSyncErrors=True,
        )
        accounts = ib.managedAccounts()
        summary = {
            row.tag: row.value
            for row in ib.accountSummary()
            if row.tag in {"NetLiquidation", "TotalCashValue", "BuyingPower"}
        }
        print(json.dumps({
            "ok": True,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "server_version": ib.client.serverVersion(),
            "managed_accounts": accounts,
            "summary": summary,
        }, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "error": f"{type(exc).__name__}: {exc}",
        }, indent=2), file=sys.stderr)
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
