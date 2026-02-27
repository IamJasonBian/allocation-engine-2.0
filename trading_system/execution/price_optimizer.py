"""
Price Optimizer — calculates optimal limit price given spread, urgency, and time of day.
"""

from datetime import datetime

try:
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")
except ImportError:
    ET = None


class PriceOptimizer:
    """Calculate optimal limit prices based on market microstructure."""

    def optimal_limit_price(self, side, bid, ask, urgency=0.5,
                            time_of_day=None) -> float:
        """Calculate the optimal limit price.

        Args:
            side: "buy" or "sell"
            bid: Current best bid
            ask: Current best ask
            urgency: 0.0 (patient) to 1.0 (must fill now)
            time_of_day: "open", "early", "midday", or "close" (auto-detected if None)

        Returns:
            Optimal limit price, or None if spread > 2%.
        """
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None

        mid = (bid + ask) / 2.0
        spread = ask - bid
        spread_pct = spread / mid if mid > 0 else 0

        # Reject if spread > 2%
        if spread_pct > 0.02:
            return None

        if time_of_day is None:
            time_of_day = self.get_time_of_day()

        # At open, be more conservative (shift toward passive side)
        urgency_adj = urgency
        if time_of_day == "open":
            urgency_adj = max(0.0, urgency - 0.3)
        elif time_of_day == "early":
            urgency_adj = max(0.0, urgency - 0.15)

        if side.lower() == "buy":
            # Low urgency: near bid. High urgency: near mid+
            passive = bid + spread * 0.25
            aggressive = mid + spread * 0.25
            price = passive + (aggressive - passive) * urgency_adj
        else:
            # Low urgency: near ask. High urgency: near mid-
            passive = ask - spread * 0.25
            aggressive = mid - spread * 0.25
            price = passive + (aggressive - passive) * urgency_adj

        return round(price, 2)

    def get_time_of_day(self) -> str:
        """Determine market time-of-day category based on current ET time.

        Returns:
            "open", "early", "midday", or "close".
        """
        try:
            if ET:
                now = datetime.now(ET)
            else:
                now = datetime.now()

            hour = now.hour
            minute = now.minute
            time_minutes = hour * 60 + minute

            if time_minutes < 585:        # Before 9:45 (market opens 9:30)
                return "open"
            elif time_minutes < 615:      # 9:45 - 10:15
                return "early"
            elif time_minutes < 930:      # 10:15 - 15:30
                return "midday"
            else:                         # 15:30 - 16:00
                return "close"

        except Exception:
            return "midday"
