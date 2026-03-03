"""Robinhood trading client — wraps robin-stocks with server-side TOTP auth."""

import logging
import os
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
        """Download the pickle from Netlify Blobs if not already on disk."""
        if os.path.isfile(self._pickle_path):
            log.info("[pickle] Local pickle exists at %s, skipping blob download",
                     self._pickle_path)
            return
        log.info("[pickle] No local pickle. Attempting blob store restore...")
        restored = download_pickle(self._pickle_path)
        if restored:
            log.info("[pickle] Restored session pickle from blob store")
            slack_notify(
                ":floppy_disk: FlipActivate: allocation-engine-2.0 — "
                "Restored Robinhood session pickle from blob store"
            )
        else:
            log.warning("[pickle] No pickle in blob store — fresh login required")

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
        # Session is authenticated — do a lightweight check.
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
