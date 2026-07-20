"""Robinhood trading client — robin-stocks reads with box-vended auth.

This client NEVER authenticates to Robinhood itself (no rh.login, no TOTP,
no pickle/device-token handling). The auth-service box owns the credentials
and session; we fetch its access token (sqlite-cached via app.box_session)
and inject it into robin_stocks' request session.
"""

import logging
from datetime import datetime, timedelta, timezone

import robin_stocks.robinhood as rh

from app.brokers.base import BrokerClient
from app.enums import OrderType

log = logging.getLogger(__name__)

# Cache instrument URL -> symbol to avoid repeated API calls
_instrument_cache: dict[str, str] = {}
_init_phase: str = "not_started"


def _symbol_from_instrument(instrument_url: str) -> str:
    """Resolve an instrument URL to a ticker symbol, with caching."""
    if instrument_url in _instrument_cache:
        return _instrument_cache[instrument_url]
    try:
        data = rh.stocks.get_instrument_by_url(instrument_url)
        symbol = data.get("symbol", "UNKNOWN") if data else "UNKNOWN"
    except Exception:
        symbol = "UNKNOWN"
    _instrument_cache[instrument_url] = symbol
    return symbol


def seconds_until_hour_et(hour: int = 11) -> float:
    """Compute seconds from now until the next occurrence of `hour`:00 ET."""
    utc_now = datetime.now(timezone.utc)
    # Approximate US Eastern DST: second Sunday in March – first Sunday in November
    year = utc_now.year
    mar1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    dst_start = mar1 + timedelta(days=(6 - mar1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    dst_end = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    offset = timedelta(hours=-4) if dst_start <= utc_now < dst_end else timedelta(hours=-5)

    now_et = utc_now.astimezone(timezone(offset))
    target = now_et.replace(hour=hour, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    return (target - now_et).total_seconds()


class RobinhoodTrader(BrokerClient):
    def __init__(
        self,
        email: str = "",
        password: str = "",
        totp_secret: str = "",
        pickle_name: str = "",
        account_number: str = "",
    ):
        # email is kept for status reporting; password/totp/pickle are legacy
        # arguments accepted (and ignored) so get_broker() stays unchanged —
        # authentication lives exclusively in the auth-service box.
        self.email = email
        self.account_number = account_number
        self._authenticated = False

        global _init_phase
        _init_phase = "box_auth"
        log.info("[rh] Authenticating via auth-service box (no local login)...")
        try:
            self._box_auth()
        except Exception:
            log.exception("[rh] Box auth failed at init — engine retries each tick")
        _init_phase = f"init_done_auth={self._authenticated}"
        log.info("[rh] __init__ complete (authenticated=%s)", self._authenticated)

    # -- authentication (box-vended token only) ------------------------------

    def _box_auth(self, force: bool = False) -> bool:
        """Fetch the box token (sqlite-first) and inject it into robin_stocks.

        Auth calls route through the box and are exempt from the
        destructive-action gates (those guard order payloads only).
        """
        from app.box_session import get_box_token
        tok = get_box_token(force=force)
        if not tok or not tok.get("token"):
            self._authenticated = False
            return False
        rh.authentication.set_login_state(True)
        rh.authentication.update_session(
            "Authorization",
            f"{tok.get('token_type') or 'Bearer'} {tok['token']}",
        )
        if not self.account_number and tok.get("account_number"):
            self.account_number = tok["account_number"]
        if not self._authenticated:
            # Only announce transitions; cache-hit re-injections stay quiet.
            log.info("[rh] Injected box token (account=%s, expires_at=%s)",
                     tok.get("account_number", "?"), tok.get("expires_at"))
        self._authenticated = True
        return True

    def _login(self):
        """Force-refresh the box token. Never runs a local Robinhood login."""
        if not self._box_auth(force=True):
            raise RuntimeError(
                "auth-service box could not vend a Robinhood token — "
                "see [box-session] logs")

    @property
    def in_device_challenge_mode(self) -> bool:
        """Local device challenges no longer exist — the box owns auth."""
        return False

    def _ensure_auth(self):
        """Keep the injected token fresh; sqlite-first, box only on expiry."""
        if not self._box_auth():
            raise RuntimeError("not authenticated — box token unavailable")

    # -- account / positions ------------------------------------------------

    def account(self) -> dict:
        self._ensure_auth()
        profile = rh.profiles.load_account_profile()
        portfolio = rh.profiles.load_portfolio_profile()
        if not profile or not portfolio:
            log.warning("Robinhood returned empty profile/portfolio — forcing re-auth")
            self._authenticated = False
            self._login()
            profile = rh.profiles.load_account_profile() or {}
            portfolio = rh.profiles.load_portfolio_profile() or {}
        return {
            "equity": float(portfolio.get("equity", 0)),
            "cash": float(profile.get("cash", 0)),
            "buying_power": float(profile.get("buying_power", 0)),
            "portfolio_value": float(portfolio.get("market_value", 0)),
        }

    def positions(self) -> list[dict]:
        self._ensure_auth()
        raw = rh.account.get_all_positions()
        result = []
        for pos in raw:
            qty = float(pos.get("quantity", 0))
            if qty == 0:
                continue

            symbol = _symbol_from_instrument(pos.get("instrument", ""))
            avg_buy = float(pos.get("average_buy_price", 0))

            try:
                quote = rh.stocks.get_latest_price(symbol)
                current_price = float(quote[0]) if quote and quote[0] else avg_buy
            except Exception:
                current_price = avg_buy

            market_value = qty * current_price
            cost_basis = qty * avg_buy
            unrealized_pl = market_value - cost_basis
            unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis > 0 else 0.0

            result.append({
                "symbol": symbol,
                "qty": qty,
                "side": "long",
                "market_value": round(market_value, 2),
                "avg_entry": avg_buy,
                "unrealized_pl": round(unrealized_pl, 2),
                "unrealized_pl_pct": round(unrealized_pl_pct, 4),
            })
        return result

    def open_orders(self) -> list[dict]:
        self._ensure_auth()
        # Avoid rh.orders.get_all_open_stock_orders() — it crashes on orders
        # missing a 'cancel' key (robin_stocks bug).  Fetch all orders and
        # filter to open states ourselves.
        all_orders = rh.orders.get_all_stock_orders()
        _open_states = {"queued", "unconfirmed", "confirmed", "partially_filled"}
        raw = [o for o in (all_orders or [])
               if isinstance(o, dict) and o.get("state") in _open_states]
        result = []
        for o in raw:
            symbol = _symbol_from_instrument(o.get("instrument", ""))
            result.append({
                "id": o.get("id"),
                "symbol": symbol,
                "side": o.get("side", "").upper(),
                "qty": float(o.get("quantity", 0)),
                "type": o.get("type", "market"),
                "limit_price": float(o["price"]) if o.get("price") else None,
                "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
                "status": o.get("state", "unknown"),
            })
        return result

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: dict) -> dict | None:
        self._ensure_auth()
        symbol = order["symbol"]
        side = order["side"].lower()
        qty = float(order["quantity"])
        otype = order.get("order_type", OrderType.MARKET).lower()
        limit_px = order.get("limit_price")
        market_hours = order.get("market_hours", "regular_hours")

        try:
            if otype == OrderType.LIMIT and limit_px:
                result = rh.orders.order(
                    symbol, qty, side,
                    limitPrice=float(limit_px),
                    timeInForce="gtc", jsonify=True,
                    market_hours=market_hours,
                )
            else:
                result = rh.orders.order(
                    symbol, qty, side,
                    timeInForce="gtc", jsonify=True,
                    market_hours=market_hours,
                )

            if result and result.get("id"):
                log.info("RH order submitted: %s %s %s @ %s -> %s",
                         side, qty, symbol, limit_px or "MKT", result["id"])
                return {
                    "id": result["id"],
                    "symbol": symbol,
                    "status": result.get("state"),
                }
            return None

        except Exception as e:
            log.error("Robinhood order error for %s %s %s: %s", side, qty, symbol, e)
            return None

    def cancel_order(self, order_id: str):
        self._ensure_auth()
        rh.orders.cancel_stock_order(order_id)
        log.info("Cancelled RH order %s", order_id)

    def cancel_all(self):
        self._ensure_auth()
        rh.orders.cancel_all_stock_orders()
        log.info("All RH stock orders cancelled")

    # -- order history & P&L ------------------------------------------------

    def order_history(self, limit: int = 50) -> list[dict]:
        """Return recent filled/completed orders."""
        self._ensure_auth()
        all_orders = rh.orders.get_all_stock_orders()
        result = []
        for o in (all_orders or []):
            if not isinstance(o, dict):
                continue
            symbol = _symbol_from_instrument(o.get("instrument", ""))
            avg_price = o.get("average_price")
            result.append({
                "id": o.get("id"),
                "symbol": symbol,
                "side": o.get("side", "").upper(),
                "quantity": float(o.get("quantity", 0)),
                "filled_quantity": float(o.get("cumulative_quantity", 0)),
                "price": float(avg_price) if avg_price else None,
                "limit_price": float(o["price"]) if o.get("price") else None,
                "stop_price": float(o["stop_price"]) if o.get("stop_price") else None,
                "type": o.get("type", "market"),
                "state": o.get("state", "unknown"),
                "created_at": o.get("created_at", ""),
                "updated_at": o.get("updated_at", ""),
            })
            if len(result) >= limit:
                break
        return result

    def realized_pnl(self, days: int = 30) -> dict:
        """Compute realized P&L from filled orders within the last N days."""
        self._ensure_auth()
        all_orders = rh.orders.get_all_stock_orders()
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        buys: dict[str, list] = {}
        sells: dict[str, list] = {}
        total_buy_volume = 0.0
        total_sell_volume = 0.0

        for o in (all_orders or []):
            if not isinstance(o, dict):
                continue
            if o.get("state") != "filled":
                continue
            created = o.get("created_at", "")
            if not created:
                continue
            try:
                order_time = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if order_time < cutoff:
                continue

            symbol = _symbol_from_instrument(o.get("instrument", ""))
            qty = float(o.get("cumulative_quantity", 0))
            avg_price = o.get("average_price")
            if not avg_price or qty == 0:
                continue
            price = float(avg_price)
            side = o.get("side", "")
            volume = qty * price

            entry = {"qty": qty, "price": price, "time": created}
            if side == "buy":
                buys.setdefault(symbol, []).append(entry)
                total_buy_volume += volume
            elif side == "sell":
                sells.setdefault(symbol, []).append(entry)
                total_sell_volume += volume

        # Compute per-symbol realized P&L using average cost basis
        symbols_pnl = []
        total_realized = 0.0
        all_symbols = set(list(buys.keys()) + list(sells.keys()))
        for sym in sorted(all_symbols):
            sym_buys = buys.get(sym, [])
            sym_sells = sells.get(sym, [])
            total_bought_qty = sum(b["qty"] for b in sym_buys)
            total_bought_vol = sum(b["qty"] * b["price"] for b in sym_buys)
            total_sold_qty = sum(s["qty"] for s in sym_sells)
            total_sold_vol = sum(s["qty"] * s["price"] for s in sym_sells)
            avg_buy_price = total_bought_vol / total_bought_qty if total_bought_qty > 0 else 0
            avg_sell_price = total_sold_vol / total_sold_qty if total_sold_qty > 0 else 0
            matched_qty = min(total_bought_qty, total_sold_qty)
            realized = matched_qty * (avg_sell_price - avg_buy_price) if matched_qty > 0 else 0

            total_realized += realized
            symbols_pnl.append({
                "symbol": sym,
                "realizedPnL": round(realized, 2),
                "totalBought": round(total_bought_vol, 2),
                "totalSold": round(total_sold_vol, 2),
                "buyCount": len(sym_buys),
                "sellCount": len(sym_sells),
                "avgBuyPrice": round(avg_buy_price, 4),
                "avgSellPrice": round(avg_sell_price, 4),
                "remainingShares": round(total_bought_qty - total_sold_qty, 4),
            })

        return {
            "totalRealizedPnL": round(total_realized, 2),
            "totalBuyVolume": round(total_buy_volume, 2),
            "totalSellVolume": round(total_sell_volume, 2),
            "days": days,
            "symbols": symbols_pnl,
        }

    # -- options ------------------------------------------------------------

    def options_positions(self) -> list[dict]:
        """Return current options positions."""
        self._ensure_auth()
        try:
            raw = rh.options.get_open_option_positions()
        except Exception:
            log.exception("Failed to fetch options positions")
            return []

        result = []
        for pos in (raw or []):
            if not isinstance(pos, dict):
                continue
            qty = float(pos.get("quantity", 0))
            if qty == 0:
                continue

            chain_symbol = pos.get("chain_symbol", "")
            # RH's position.type is direction (long/short), not contract kind.
            position_type = pos.get("type", "")
            avg_price = float(pos.get("average_price", 0)) / 100  # RH stores in cents
            trade_value_multiplier = float(pos.get("trade_value_multiplier", 100))

            # Get option instrument details
            option_data = {}
            option_url = pos.get("option", "")
            if option_url:
                try:
                    option_data = rh.options.get_option_instrument_data_by_id(
                        pos.get("option_id", "")
                    ) or {}
                except Exception:
                    pass

            strike = float(option_data.get("strike_price", 0))
            expiration = option_data.get("expiration_date", "")
            # The instrument's `type` is the contract kind (call/put). Fall back
            # to position_type only if the instrument lookup failed — that path
            # preserves the old (broken) behaviour rather than silently emitting
            # an empty string.
            option_type = option_data.get("type") or position_type

            # Calculate DTE
            dte = 0
            if expiration:
                try:
                    exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
                    dte = (exp_date - datetime.now(timezone.utc).date()).days
                except (ValueError, TypeError):
                    pass

            # Get market data for this option (includes greeks). The RH
            # endpoint requires call/put — we pass option_type which now holds
            # the instrument's contract kind, not direction.
            mark_price = avg_price
            greeks = {"delta": None, "gamma": None, "theta": None, "vega": None, "iv": None}
            underlying_price = None
            try:
                market_data = rh.options.get_option_market_data(
                    chain_symbol, expiration, str(strike),
                    option_type, info=None
                )
                if market_data and isinstance(market_data, list) and market_data[0]:
                    md = market_data[0]
                    mark_price = float(md.get("mark_price", avg_price))
                    for g in ("delta", "gamma", "theta", "vega"):
                        val = md.get(g)
                        if val is not None:
                            try:
                                greeks[g] = round(float(val), 6)
                            except (ValueError, TypeError):
                                pass
                    iv_val = md.get("implied_volatility")
                    if iv_val is not None:
                        try:
                            greeks["iv"] = round(float(iv_val), 6)
                        except (ValueError, TypeError):
                            pass
                    hp = md.get("high_fill_rate_buy_price") or md.get("adjusted_mark_price")
                    # Underlying price from instrument or latest quote
                    up = md.get("underlying_price")
                    if up is not None:
                        try:
                            underlying_price = round(float(up), 4)
                        except (ValueError, TypeError):
                            pass
            except Exception:
                pass

            # Fall back to latest stock price for underlying
            if underlying_price is None and chain_symbol:
                try:
                    prices = rh.stocks.get_latest_price(chain_symbol)
                    if prices and prices[0]:
                        underlying_price = round(float(prices[0]), 4)
                except Exception:
                    pass

            cost_basis = qty * avg_price * trade_value_multiplier
            current_value = qty * mark_price * trade_value_multiplier
            unrealized_pl = current_value - cost_basis

            result.append({
                "chain_symbol": chain_symbol,
                "option_type": option_type,        # call/put (contract kind)
                "position_type": position_type,    # long/short (direction)
                "strike": strike,
                "expiration": expiration,
                "dte": dte,
                "quantity": qty,
                "avg_price": round(avg_price, 4),
                "mark_price": round(mark_price, 4),
                "multiplier": trade_value_multiplier,
                "cost_basis": round(cost_basis, 2),
                "current_value": round(current_value, 2),
                "unrealized_pl": round(unrealized_pl, 2),
                "unrealized_pl_pct": round(unrealized_pl / cost_basis, 4) if cost_basis else 0,
                "underlying_price": underlying_price,
                "greeks": greeks,
            })
        return result

    _OPEN_OPTION_STATES = {"queued", "confirmed", "partially_filled", "pending"}

    def options_orders(self, limit: int = 50, open_only: bool = False) -> list[dict]:
        """Return recent options orders.

        Args:
            limit: Maximum number of orders to return.
            open_only: If True, only return orders in open states
                       (queued, confirmed, partially_filled, pending).
        """
        self._ensure_auth()
        try:
            raw = rh.orders.get_all_option_orders()
        except Exception:
            log.exception("Failed to fetch options orders")
            return []

        result = []
        for o in (raw or []):
            if not isinstance(o, dict):
                continue
            if open_only and o.get("state", "") not in self._OPEN_OPTION_STATES:
                continue
            legs = []
            for leg in o.get("legs", []):
                legs.append({
                    "side": leg.get("side", ""),
                    "position_effect": leg.get("position_effect", ""),
                    "quantity": float(leg.get("quantity", 0)) if leg.get("quantity") else 0,
                    "strike": float(leg.get("strike_price", 0)) if leg.get("strike_price") else 0,
                    "expiration": leg.get("expiration_date", ""),
                    "option_type": leg.get("option_type", ""),
                    "chain_symbol": o.get("chain_symbol", ""),
                })

            result.append({
                "order_id": o.get("id", ""),
                "state": o.get("state", ""),
                "quantity": float(o.get("quantity", 0)),
                "price": float(o.get("price", 0)) if o.get("price") else 0,
                "premium": float(o.get("premium", 0)) if o.get("premium") else 0,
                "processed_premium": float(o.get("processed_premium", 0)) if o.get("processed_premium") else 0,
                "direction": o.get("direction", ""),
                "order_type": o.get("type", ""),
                "trigger": o.get("trigger", ""),
                "time_in_force": o.get("time_in_force", ""),
                "opening_strategy": o.get("opening_strategy") or o.get("closing_strategy") or "",
                "created_at": o.get("created_at", ""),
                "updated_at": o.get("updated_at", ""),
                "legs": legs,
            })
            if len(result) >= limit:
                break
        return result

    # -- funding: linked-bank ACH deposit/withdraw ---------------------------

    def linked_bank_accounts(self) -> list[dict]:
        """List bank accounts linked for ACH transfer."""
        self._ensure_auth()
        raw = rh.account.get_linked_bank_accounts() or []
        return [
            {
                "id": b.get("id"),
                "bank_name": b.get("bank_name") or b.get("name"),
                "account_type": b.get("type"),
                "verified": b.get("verified"),
            }
            for b in raw if isinstance(b, dict)
        ]

    def deposit(self, amount: float, ach_relationship: str = "", **kwargs) -> dict | None:
        """Deposit funds from a linked bank account into Robinhood via ACH."""
        if not ach_relationship:
            raise ValueError("ach_relationship is required for Robinhood deposits")
        self._ensure_auth()
        result = rh.account.deposit_funds_to_robinhood_account(ach_relationship, amount)
        if result and result.get("id"):
            log.info("RH deposit initiated: $%s -> %s", amount, result["id"])
            return {"id": result["id"], "amount": float(amount), "state": result.get("state")}
        log.error("RH deposit failed for $%s: %s", amount, result)
        return None

    def withdraw(self, amount: float, ach_relationship: str = "", **kwargs) -> dict | None:
        """Withdraw funds from Robinhood to a linked bank account via ACH."""
        if not ach_relationship:
            raise ValueError("ach_relationship is required for Robinhood withdrawals")
        self._ensure_auth()
        result = rh.account.withdrawl_funds_to_bank_account(ach_relationship, amount)
        if result and result.get("id"):
            log.info("RH withdrawal initiated: $%s -> %s", amount, result["id"])
            return {"id": result["id"], "amount": float(amount), "state": result.get("state")}
        log.error("RH withdrawal failed for $%s: %s", amount, result)
        return None

    def transfer_history(self, direction: str | None = None, **kwargs) -> list[dict]:
        """Return past ACH transfers (direction: 'deposit', 'withdraw', or None for both)."""
        self._ensure_auth()
        raw = rh.account.get_bank_transfers(direction=direction) or []
        return [
            {
                "id": t.get("id"),
                "amount": float(t.get("amount", 0) or 0),
                "direction": t.get("direction"),
                "state": t.get("state"),
                "created_at": t.get("created_at"),
            }
            for t in raw if isinstance(t, dict)
        ]

    # -- auth status --------------------------------------------------------

    def auth_status(self) -> dict:
        """Return current authentication state (box-vended token)."""
        from app.box_session import cached_token_status
        return {
            "authenticated": self._authenticated,
            "device_challenge_pending": False,
            "email": self.email,
            "auth_source": "auth-service-box",
            **cached_token_status(),
        }
