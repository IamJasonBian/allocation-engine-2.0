"""Robinhood trading client — wraps robin-stocks with server-side TOTP auth."""

import logging
import os
import pickle
import threading
from datetime import datetime, timedelta, timezone

import pyotp
import robin_stocks.robinhood as rh

from app.pickle_store import download_pickle, upload_pickle
from app.slack import notify as slack_notify

log = logging.getLogger(__name__)

# Cache instrument URL -> symbol to avoid repeated API calls
_instrument_cache: dict[str, str] = {}

# Login timeout: max seconds to wait for rh.login() (guards against the
# infinite polling loop inside robin_stocks' _validate_sherrif_id).
_LOGIN_TIMEOUT = 180


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


def _pickle_path(pickle_name: str) -> str:
    """Compute the full path to the robin_stocks pickle file."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".tokens", f"robinhood{pickle_name}.pickle")


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


class RobinhoodTrader:
    def __init__(
        self,
        email: str,
        password: str,
        totp_secret: str = "",
        pickle_name: str = "taipei_session",
    ):
        self.email = email
        self.password = password
        self.totp_secret = totp_secret
        self.pickle_name = pickle_name
        self._authenticated = False
        self._pickle_path = _pickle_path(pickle_name)
        self._device_challenge_mode = False

        # Restore pickle from blob store before attempting login
        self._restore_pickle_from_blob()

        try:
            self._login()
        except Exception:
            log.warning("Initial login failed — engine will retry each tick")

    # -- session pickle persistence -----------------------------------------

    def _restore_pickle_from_blob(self):
        """Ensure a pickle with the trusted device_token exists before login.

        Priority:
          1. Local pickle already on disk → use it
          2. Download full pickle from Netlify Blobs → use it
          3. Seed a stub pickle with RH_DEVICE_TOKEN env var so robin_stocks
             uses our known device_token instead of generating a random one
        """
        if os.path.isfile(self._pickle_path):
            log.info("[pickle] Local pickle exists at %s", self._pickle_path)
            return

        log.info("[pickle] No local pickle. Attempting blob store restore...")
        if download_pickle(self._pickle_path):
            log.info("[pickle] Restored session pickle from blob store")
            slack_notify(
                ":floppy_disk: FlipActivate: allocation-engine-2.0 — "
                "Restored Robinhood session pickle from blob store"
            )
            return

        # No blob either — seed a stub pickle with the static device_token
        # so robin_stocks reuses our approved device instead of generating
        # a random one that triggers Robinhood's device verification.
        from app.config import Config
        device_token = Config.RH_DEVICE_TOKEN
        if not device_token:
            log.warning("[pickle] RH_DEVICE_TOKEN not set — "
                        "robin_stocks will generate a random device_token "
                        "(may trigger device challenge)")
            return

        os.makedirs(os.path.dirname(self._pickle_path), exist_ok=True)
        stub = {
            "device_token": device_token,
            "access_token": "",
            "token_type": "Bearer",
            "refresh_token": "",
        }
        with open(self._pickle_path, "wb") as f:
            pickle.dump(stub, f)
        log.info("[pickle] Seeded stub pickle with static device_token=%s...%s",
                 device_token[:8], device_token[-4:])

    def _upload_pickle_to_blob(self):
        """Upload the current pickle to Netlify Blobs after successful login."""
        if os.path.isfile(self._pickle_path):
            if upload_pickle(self._pickle_path):
                log.info("[pickle] Uploaded session pickle to blob store")
            else:
                log.warning("[pickle] Failed to upload pickle to blob store")

    # -- authentication -----------------------------------------------------

    def _login(self):
        """Authenticate with Robinhood using stored session or fresh TOTP login.

        Runs rh.login() in a child thread with a timeout to guard against
        the infinite polling loop in robin_stocks' device verification.
        """
        mfa_code = None
        if self.totp_secret:
            totp = pyotp.TOTP(self.totp_secret)
            mfa_code = totp.now()

        login_result: list = [None]
        login_error: list = [None]

        def _do_login():
            try:
                login_result[0] = rh.login(
                    self.email,
                    self.password,
                    mfa_code=mfa_code,
                    store_session=True,
                    pickle_name=self.pickle_name,
                )
            except Exception as e:
                login_error[0] = e

        login_thread = threading.Thread(target=_do_login, name="rh-login", daemon=True)
        login_thread.start()
        login_thread.join(timeout=_LOGIN_TIMEOUT)

        # --- Thread timed out (stuck in device challenge infinite loop) ---
        if login_thread.is_alive():
            self._authenticated = False
            self._device_challenge_mode = True
            log.error("[login] Timed out after %ds waiting for device approval",
                      _LOGIN_TIMEOUT)
            slack_notify(
                f"<!channel> :rotating_light: FlipActivate: allocation-engine-2.0 — "
                f"Robinhood login TIMED OUT for {self.email}. "
                "Device challenge triggered — approve in Robinhood app. "
                "Will retry at next scheduled window."
            )
            raise RuntimeError("Robinhood login timed out — device challenge pending")

        # --- Thread raised an exception ---
        if login_error[0]:
            self._authenticated = False
            log.error("Robinhood login failed: %s", login_error[0])
            slack_notify(
                f"<!channel> :rotating_light: FlipActivate: allocation-engine-2.0 — "
                f"Robinhood login FAILED for {self.email}: {login_error[0]}"
            )
            raise login_error[0]

        # --- Thread returned a result ---
        result = login_result[0]
        if result:
            self._authenticated = True
            self._device_challenge_mode = False
            log.info("Robinhood login successful for %s", self.email)
            slack_notify(
                f"<!channel> :white_check_mark: FlipActivate: allocation-engine-2.0 — "
                f"Robinhood login successful for {self.email}"
            )
            self._upload_pickle_to_blob()
        else:
            self._authenticated = False
            log.error("Robinhood login returned empty result — "
                      "check Robinhood app for device approval")
            slack_notify(
                f"<!channel> :warning: FlipActivate: allocation-engine-2.0 — "
                f"Robinhood login returned empty result for {self.email} "
                f"— check Robinhood app for device approval"
            )
            raise RuntimeError(
                "Robinhood login empty — device approval may be required"
            )

    @property
    def in_device_challenge_mode(self) -> bool:
        """True when login failed due to a device challenge timeout."""
        return self._device_challenge_mode

    def _ensure_auth(self):
        """Re-authenticate if session has expired."""
        if not self._authenticated:
            if self._device_challenge_mode:
                raise RuntimeError("Cannot authenticate — device challenge pending")
            self._login()
            return
        # Tolerate transient errors (429, network blips) so we don't
        # trigger a full re-login that could hit the device challenge loop.
        try:
            rh.profiles.load_account_profile()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "Too Many Requests" in err_str:
                log.warning("Robinhood 429 during session check — "
                            "skipping re-auth (session likely still valid)")
                return
            log.warning("Robinhood session expired, re-authenticating...")
            slack_notify(
                "<!channel> :warning: FlipActivate: allocation-engine-2.0 — "
                "Robinhood session expired, re-authenticating..."
            )
            self._authenticated = False
            self._login()

    # -- account / positions ------------------------------------------------

    def account(self) -> dict:
        self._ensure_auth()
        profile = rh.profiles.load_account_profile()
        portfolio = rh.profiles.load_portfolio_profile()
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
        otype = order.get("order_type", "market").lower()
        limit_px = order.get("limit_price")

        try:
            if otype == "limit" and limit_px:
                result = rh.orders.order(
                    symbol, qty, side,
                    limitPrice=float(limit_px),
                    timeInForce="gtc", jsonify=True,
                )
            else:
                result = rh.orders.order(
                    symbol, qty, side,
                    timeInForce="gtc", jsonify=True,
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
            option_type = pos.get("type", "")
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

            # Calculate DTE
            dte = 0
            if expiration:
                try:
                    exp_date = datetime.strptime(expiration, "%Y-%m-%d").date()
                    dte = (exp_date - datetime.now(timezone.utc).date()).days
                except (ValueError, TypeError):
                    pass

            # Get market data for this option
            mark_price = avg_price
            try:
                market_data = rh.options.get_option_market_data(
                    chain_symbol, expiration, str(strike),
                    option_type, info=None
                )
                if market_data and isinstance(market_data, list) and market_data[0]:
                    md = market_data[0]
                    mark_price = float(md.get("mark_price", avg_price))
            except Exception:
                pass

            cost_basis = qty * avg_price * trade_value_multiplier
            current_value = qty * mark_price * trade_value_multiplier
            unrealized_pl = current_value - cost_basis

            result.append({
                "chain_symbol": chain_symbol,
                "option_type": option_type,
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
            })
        return result

    def options_orders(self, limit: int = 50) -> list[dict]:
        """Return recent options orders."""
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

    # -- auth status --------------------------------------------------------

    def auth_status(self) -> dict:
        """Return current authentication state."""
        return {
            "authenticated": self._authenticated,
            "device_challenge_pending": self._device_challenge_mode,
            "email": self.email,
        }
