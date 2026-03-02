"""Robinhood trading client — wraps robin-stocks with server-side TOTP auth."""

import logging
import robin_stocks.robinhood as rh
import pyotp

from app.models import (
    AccountSummary, FilledOrder, OpenOrder, OptionOrder,
    OptionPositionData, Order, OrderResult, Position,
)
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

    def recent_orders(self, limit: int = 50) -> list[FilledOrder]:
        """Return recent filled/cancelled stock orders from Robinhood."""
        self._ensure_auth()
        try:
            raw = rh.orders.get_all_stock_orders()
            result: list[FilledOrder] = []
            for o in raw[:limit]:
                state = o.get("state", "")
                if state not in ("filled", "cancelled", "partially_filled"):
                    continue
                symbol = _symbol_from_instrument(o.get("instrument", ""))
                result.append(FilledOrder(
                    id=o.get("id", ""),
                    symbol=symbol,
                    side=o.get("side", "").upper(),
                    qty=float(o.get("quantity", 0)),
                    order_type=o.get("type", "market"),
                    limit_price=float(o["price"]) if o.get("price") else None,
                    stop_price=float(o["stop_price"]) if o.get("stop_price") else None,
                    average_price=float(o["average_price"]) if o.get("average_price") else None,
                    filled_qty=float(o.get("cumulative_quantity", 0)),
                    status=state,
                    created_at=o.get("created_at", ""),
                    updated_at=o.get("updated_at", ""),
                ))
            return result
        except Exception:
            log.exception("Failed to fetch recent orders from Robinhood")
            return []

    def option_positions(self) -> list[OptionPositionData]:
        """Return current option positions with Greeks from Robinhood."""
        self._ensure_auth()
        try:
            raw = rh.options.get_open_option_positions()
            if not raw:
                return []

            result: list[OptionPositionData] = []
            for pos in raw:
                qty = float(pos.get("quantity", 0))
                if qty == 0:
                    continue

                # Fetch the option instrument details for Greeks
                option_id = pos.get("option_id") or pos.get("option", "").split("/")[-2] if pos.get("option") else ""
                chain_symbol = pos.get("chain_symbol", "")
                avg_price = float(pos.get("average_price", 0)) / 100  # RH stores in cents
                trade_value_multiplier = float(pos.get("trade_value_multiplier", 100))

                # Get market data for this option
                option_data = {}
                if option_id:
                    try:
                        md = rh.options.get_option_market_data_by_id(option_id)
                        if md and isinstance(md, list) and len(md) > 0:
                            option_data = md[0]
                        elif md and isinstance(md, dict):
                            option_data = md
                    except Exception:
                        log.debug("Could not fetch option market data for %s", option_id)

                # Get option instrument details
                instrument = {}
                if option_id:
                    try:
                        inst = rh.options.get_option_instrument_data_by_id(option_id)
                        if inst:
                            instrument = inst
                    except Exception:
                        log.debug("Could not fetch option instrument for %s", option_id)

                mark_price = float(option_data.get("mark_price", 0) or 0)
                iv = float(option_data.get("implied_volatility", 0) or 0)
                delta = float(option_data.get("delta", 0) or 0)
                gamma = float(option_data.get("gamma", 0) or 0)
                theta = float(option_data.get("theta", 0) or 0)
                vega = float(option_data.get("vega", 0) or 0)
                rho_val = float(option_data.get("rho", 0) or 0)
                chance = float(option_data.get("chance_of_profit_short" if qty < 0 else "chance_of_profit_long", 0) or 0)

                strike = float(instrument.get("strike_price", 0) or 0)
                expiration = instrument.get("expiration_date", "")
                option_type = instrument.get("type", "call")

                # Get underlying price
                underlying_price = 0.0
                if chain_symbol:
                    try:
                        px = rh.stocks.get_latest_price(chain_symbol)
                        underlying_price = float(px[0]) if px and px[0] else 0.0
                    except Exception:
                        pass

                position_type = "long" if qty > 0 else "short"
                cost_basis = abs(qty) * avg_price * trade_value_multiplier
                current_value = abs(qty) * mark_price * trade_value_multiplier
                unrealized_pl = current_value - cost_basis if position_type == "long" else cost_basis - current_value
                unrealized_pl_pct = unrealized_pl / cost_basis if cost_basis > 0 else 0.0

                # Break-even calculation
                if option_type == "call":
                    break_even = strike + avg_price
                else:
                    break_even = strike - avg_price

                result.append(OptionPositionData(
                    chain_symbol=chain_symbol,
                    option_type=option_type,
                    strike=strike,
                    expiration=expiration,
                    quantity=abs(qty),
                    position_type=position_type,
                    avg_price=avg_price,
                    mark_price=mark_price,
                    multiplier=trade_value_multiplier,
                    cost_basis=round(cost_basis, 2),
                    current_value=round(current_value, 2),
                    unrealized_pl=round(unrealized_pl, 2),
                    unrealized_pl_pct=round(unrealized_pl_pct, 4),
                    underlying_price=underlying_price,
                    break_even=round(break_even, 2),
                    delta=delta,
                    gamma=gamma,
                    theta=theta,
                    vega=vega,
                    rho=rho_val,
                    iv=iv,
                    chance_of_profit=chance,
                ))
            return result
        except Exception:
            log.exception("Failed to fetch option positions from Robinhood")
            return []

    def recent_option_orders(self, limit: int = 20) -> list[OptionOrder]:
        """Return recent option orders from Robinhood."""
        self._ensure_auth()
        try:
            raw = rh.orders.get_all_option_orders()
            if not raw:
                return []

            result: list[OptionOrder] = []
            for o in raw[:limit]:
                state = o.get("state", "")
                if state not in ("filled", "cancelled", "partially_filled"):
                    continue

                legs = []
                for leg in o.get("legs", []):
                    option_url = leg.get("option", "")
                    # Extract option details from the leg
                    leg_data = {
                        "side": leg.get("side", ""),
                        "position_effect": leg.get("position_effect", ""),
                        "quantity": float(leg.get("quantity", 0) or 0),
                    }
                    # Try to get strike/expiration from option instrument
                    if option_url:
                        try:
                            opt_id = option_url.rstrip("/").split("/")[-1]
                            inst = rh.options.get_option_instrument_data_by_id(opt_id)
                            if inst:
                                leg_data["strike"] = float(inst.get("strike_price", 0) or 0)
                                leg_data["expiration"] = inst.get("expiration_date", "")
                                leg_data["option_type"] = inst.get("type", "call")
                                leg_data["chain_symbol"] = inst.get("chain_symbol", "")
                        except Exception:
                            pass
                    legs.append(leg_data)

                premium = float(o.get("premium", 0) or 0)
                processed_premium = float(o.get("processed_premium", 0) or 0)
                price = float(o.get("price", 0) or 0)

                result.append(OptionOrder(
                    id=o.get("id", ""),
                    state=state,
                    quantity=float(o.get("quantity", 0)),
                    price=price,
                    premium=premium,
                    direction=o.get("direction", ""),
                    order_type=o.get("type", "limit"),
                    trigger=o.get("trigger", "immediate"),
                    time_in_force=o.get("time_in_force", "gfd"),
                    opening_strategy=o.get("opening_strategy") or "",
                    created_at=o.get("created_at", ""),
                    updated_at=o.get("updated_at", ""),
                    legs=legs,
                ))
            return result
        except Exception:
            log.exception("Failed to fetch recent option orders from Robinhood")
            return []

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
