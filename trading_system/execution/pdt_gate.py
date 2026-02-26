"""
PDT Gate — centralized Pattern Day Trading guard.

EVERY order must pass through this before reaching Robinhood.
Fails CLOSED: if PDT status is unavailable, block the order.
"""

import time
from datetime import datetime, date


class PDTGate:
    """Centralized PDT guard. All orders pass through before execution."""

    def __init__(self, trading_bot=None):
        self._trading_bot = trading_bot

    def can_place_order(self, symbol: str, side: str) -> tuple:
        """Check if an order is safe to place from a PDT perspective.

        Args:
            symbol: Stock symbol
            side: "buy" or "sell"

        Returns:
            (allowed: bool, reason: str)
        """
        if not self._trading_bot:
            return (True, "no trading bot configured — skipping PDT check")

        try:
            time.sleep(0.5)  # Rate limit protection
            pdt_info = self._trading_bot.get_pdt_status()

            if pdt_info is None:
                return (False, "PDT status unavailable — blocking as precaution")

            flagged = pdt_info.get('flagged', False)
            day_trade_count = pdt_info.get('day_trade_count', 0)

            # Step 1: If flagged, only allow sells (position-closing)
            if flagged:
                if side.lower() == 'sell':
                    return (True, "PDT flagged — allowing position-closing sell")
                else:
                    return (False, "PDT FLAGGED — only position-closing sells allowed")

            # Step 2: Check if this would create a day trade
            would_dt = self._would_create_day_trade(symbol, side)

            if would_dt and day_trade_count >= 2:
                return (False,
                        f"PDT risk: {day_trade_count}/3 day trades used, "
                        f"this {side} on {symbol} would create another")

            # Step 3: Warning if approaching limit
            if would_dt and day_trade_count == 1:
                return (True,
                        f"PDT warning: {day_trade_count}/3 day trades used, "
                        f"this {side} on {symbol} may create another (2/3)")

            # Step 4: Safe to proceed
            return (True, f"PDT OK: {day_trade_count}/3 day trades")

        except Exception as e:
            return (False, f"PDT check error — blocking as precaution: {e}")

    def _would_create_day_trade(self, symbol: str, side: str) -> bool:
        """Check if placing this order would create a day trade (round trip).

        Returns True if there's a same-day opposite fill for this symbol.
        Defensive: returns True on API failure (assume worst case).
        """
        try:
            open_orders = self._trading_bot.get_open_orders()
            if not open_orders:
                return False

            today = date.today().isoformat()
            opposite_side = 'SELL' if side.lower() == 'buy' else 'BUY'

            for order in open_orders:
                if order.get('symbol') != symbol:
                    continue
                order_side = order.get('side', '').upper()
                if order_side != opposite_side:
                    continue
                # Check if created today
                created = order.get('created_at', '')
                if isinstance(created, str) and created.startswith(today):
                    return True

            return False

        except Exception as e:
            print(f"  [pdt_gate] _would_create_day_trade error: {e}")
            return True  # Assume worst case on failure
