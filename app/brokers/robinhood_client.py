"""Robinhood trading client — wraps robin-stocks with server-side TOTP auth."""

import logging
import os
import time
import robin_stocks.robinhood as rh
import pyotp

from app.slack import notify as slack_notify

log = logging.getLogger(__name__)

# Throttle Slack alerts — only send once per 10 minutes for repeated failures
_SLACK_THROTTLE_SECS = 600
_last_slack_alert = 0.0

# Cache instrument URL -> symbol to avoid repeated API calls
_instrument_cache: dict[str, str] = {}

# Redis key for persisting the Robinhood session pickle across deploys
_REDIS_SESSION_KEY = "rh:session:pickle"


def _get_pickle_path(pickle_name: str) -> str:
    """Return the file path robin-stocks uses for its session pickle."""
    home = os.path.expanduser("~")
    return os.path.join(home, ".tokens", f"robinhood{pickle_name}.pickle")


def _get_redis_bytes_client():
    """Get a Redis client with raw bytes (no decode) for binary pickle data."""
    try:
        import redis as _redis
    except ImportError:
        return None
    host = os.getenv("REDIS_HOST")
    password = os.getenv("REDIS_PASSWORD")
    if not host:
        return None
    port = 6379
    if ":" in host:
        host, port_str = host.rsplit(":", 1)
        try:
            port = int(port_str)
        except ValueError:
            pass
    try:
        return _redis.Redis(host=host, port=port, password=password,
                            decode_responses=False)
    except Exception:
        return None


def _restore_session_from_redis(pickle_name: str):
    """Restore robin-stocks pickle file from Redis if available."""
    try:
        client = _get_redis_bytes_client()
        if not client:
            return
        data = client.get(_REDIS_SESSION_KEY)
        client.close()
        if not data:
            log.info("[session] No stored session in Redis")
            return
        path = _get_pickle_path(pickle_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        log.info("[session] Restored session pickle from Redis -> %s", path)
    except Exception:
        log.exception("[session] Failed to restore session from Redis")


def _save_session_to_redis(pickle_name: str):
    """Save robin-stocks pickle file to Redis for persistence across deploys."""
    try:
        path = _get_pickle_path(pickle_name)
        if not os.path.isfile(path):
            return
        with open(path, "rb") as f:
            data = f.read()
        client = _get_redis_bytes_client()
        if not client:
            return
        # Store with 24h TTL (matches robin-stocks token expiry)
        client.set(_REDIS_SESSION_KEY, data, ex=86400)
        client.close()
        log.info("[session] Saved session pickle to Redis (%d bytes)", len(data))
    except Exception:
        log.exception("[session] Failed to save session to Redis")


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


class RobinhoodTrader:
    def __init__(
        self,
        email: str,
        password: str,
        totp_secret: str = "",
        device_token: str = "",
        pickle_name: str = "taipei_session",
    ):
        self.email = email
        self.password = password
        self.totp_secret = totp_secret
        self.device_token = device_token
        self.pickle_name = pickle_name
        self._authenticated = False
        self._last_auth_check = 0.0
        _restore_session_from_redis(self.pickle_name)
        try:
            self._login()
        except Exception:
            log.warning("Initial login failed — engine will retry each tick")

    def _login(self):
        """Authenticate with Robinhood using stored session or fresh TOTP login."""
        mfa_code = None
        if self.totp_secret:
            totp = pyotp.TOTP(self.totp_secret)
            mfa_code = totp.now()

        kwargs = {
            "store_session": True,
            "pickle_name": self.pickle_name,
        }
        if mfa_code:
            kwargs["mfa_code"] = mfa_code
        if self.device_token:
            kwargs["device_token"] = self.device_token

        global _last_slack_alert
        try:
            result = rh.login(self.email, self.password, **kwargs)
            if result:
                self._authenticated = True
                self._last_auth_check = time.monotonic()
                log.info("Robinhood login successful for %s", self.email)
                _save_session_to_redis(self.pickle_name)
                _last_slack_alert = 0.0  # reset throttle on success
                slack_notify(
                    f"<!channel> :white_check_mark: FlipActivate: allocation-engine-2.0 — "
                    f"Robinhood login successful for {self.email}"
                )
            else:
                self._authenticated = False
                log.error("Robinhood login returned empty result — "
                          "check Robinhood app for device approval")
                now = time.monotonic()
                if (now - _last_slack_alert) >= _SLACK_THROTTLE_SECS:
                    _last_slack_alert = now
                    slack_notify(
                        f"<!channel> :warning: FlipActivate: allocation-engine-2.0 — "
                        f"Robinhood login returned empty result for {self.email} "
                        f"— check Robinhood app for device approval"
                    )
                raise RuntimeError(
                    "Robinhood login empty — device approval may be required"
                )
        except RuntimeError:
            raise
        except Exception as e:
            self._authenticated = False
            log.error("Robinhood login failed: %s", e)
            now = time.monotonic()
            if (now - _last_slack_alert) >= _SLACK_THROTTLE_SECS:
                _last_slack_alert = now
                slack_notify(
                    f"<!channel> :rotating_light: FlipActivate: allocation-engine-2.0 — "
                    f"Robinhood login FAILED for {self.email}: {e}"
                )
            raise

    def _ensure_auth(self):
        """Re-authenticate if session has expired.

        Skips the health-check probe if the last successful check was
        within 5 minutes — avoids unnecessary API calls to Robinhood.
        """
        if not self._authenticated:
            self._login()
            return
        # Only probe session health every 5 minutes
        now = time.monotonic()
        if (now - self._last_auth_check) < 300:
            return
        try:
            rh.profiles.load_account_profile()
            self._last_auth_check = now
        except Exception:
            global _last_slack_alert
            log.warning("Robinhood session expired, re-authenticating...")
            alert_now = time.monotonic()
            if (alert_now - _last_slack_alert) >= _SLACK_THROTTLE_SECS:
                _last_slack_alert = alert_now
                slack_notify(
                    "<!channel> :warning: FlipActivate: allocation-engine-2.0 — "
                    "Robinhood session expired, re-authenticating..."
                )
            self._authenticated = False
            self._last_auth_check = 0.0
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
        raw = rh.orders.get_all_open_stock_orders()
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
