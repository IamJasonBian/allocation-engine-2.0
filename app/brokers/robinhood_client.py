"""Robinhood trading client — wraps robin-stocks with server-side TOTP auth."""

import logging
import robin_stocks.robinhood as rh
import pyotp

from app.models import AccountSummary, OpenOrder, Order, OrderResult, Position
from app.slack import notify as slack_notify

log = logging.getLogger(__name__)

# Cache instrument URL -> symbol to avoid repeated API calls
_instrument_cache: dict[str, str] = {}


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
        pickle_name: str = "taipei_session",
    ):
        self.email = email
        self.password = password
        self.totp_secret = totp_secret
        self.pickle_name = pickle_name
        self._authenticated = False
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

        try:
            result = rh.login(
                self.email,
                self.password,
                mfa_code=mfa_code,
                store_session=True,
                pickle_name=self.pickle_name,
            )
            if result:
                self._authenticated = True
                log.info("Robinhood login successful for %s", self.email)
                slack_notify(
                    f"<!channel> :white_check_mark: FlipActivate: allocation-engine-2.0 — "
                    f"Robinhood login successful for {self.email}"
                )
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
        except RuntimeError:
            raise
        except Exception as e:
            self._authenticated = False
            log.error("Robinhood login failed: %s", e)
            slack_notify(
                f"<!channel> :rotating_light: FlipActivate: allocation-engine-2.0 — "
                f"Robinhood login FAILED for {self.email}: {e}"
            )
            raise

    def _ensure_auth(self):
        """Re-authenticate if session has expired."""
        if not self._authenticated:
            self._login()
            return
        try:
            rh.profiles.load_account_profile()
        except Exception:
            log.warning("Robinhood session expired, re-authenticating...")
            slack_notify(
                "<!channel> :warning: FlipActivate: allocation-engine-2.0 — "
                "Robinhood session expired, re-authenticating..."
            )
            self._authenticated = False
            self._login()

    # -- account / positions ------------------------------------------------

    def account(self) -> AccountSummary:
        self._ensure_auth()
        profile = rh.profiles.load_account_profile()
        portfolio = rh.profiles.load_portfolio_profile()
        return AccountSummary(
            equity=float(portfolio.get("equity", 0)),
            cash=float(profile.get("cash", 0)),
            buying_power=float(profile.get("buying_power", 0)),
            portfolio_value=float(portfolio.get("market_value", 0)),
        )

    def positions(self) -> list[Position]:
        self._ensure_auth()
        raw = rh.account.get_all_positions()
        result: list[Position] = []
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

            result.append(Position(
                symbol=symbol,
                qty=qty,
                side="long",
                market_value=round(market_value, 2),
                avg_entry=avg_buy,
                unrealized_pl=round(unrealized_pl, 2),
                unrealized_pl_pct=round(unrealized_pl_pct, 4),
            ))
        return result

    def open_orders(self) -> list[OpenOrder]:
        self._ensure_auth()
        raw = rh.orders.get_all_open_stock_orders()
        result: list[OpenOrder] = []
        for o in raw:
            symbol = _symbol_from_instrument(o.get("instrument", ""))
            result.append(OpenOrder(
                id=o.get("id", ""),
                symbol=symbol,
                side=o.get("side", "").upper(),
                qty=float(o.get("quantity", 0)),
                order_type=o.get("type", "market"),
                limit_price=float(o["price"]) if o.get("price") else None,
                stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
                status=o.get("state", "unknown"),
            ))
        return result

    # -- order submission ---------------------------------------------------

    def submit_order(self, order: Order) -> OrderResult | None:
        self._ensure_auth()
        symbol = order.symbol
        side = order.side.lower()
        qty = order.qty
        otype = order.order_type.lower()
        limit_px = order.limit_price

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
                return OrderResult(
                    id=result["id"],
                    symbol=symbol,
                    status=result.get("state"),
                )
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
