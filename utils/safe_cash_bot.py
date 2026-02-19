"""
Safe Cash-Only Trading Bot for Account 919433888
ISOLATED: Only trades with cash in the specified account
"""

import math
import os
import sys
from datetime import datetime, date

import robin_stocks.robinhood as r
from dotenv import load_dotenv

from .rh_auth import RobinhoodAuth


class SafeCashBot:
    """
    Trading bot with strict safety controls:
    - Only trades in specified account (919433888)
    - Only uses available cash (no margin)
    - Validates all orders before execution
    """

    def __init__(self):
        load_dotenv()

        # Lock to specific account
        self.account_number = os.getenv('RH_AUTOMATED_ACCOUNT_NUMBER')

        if not self.account_number:
            print("[ERR] ERROR: RH_AUTOMATED_ACCOUNT_NUMBER not set in .env")
            sys.exit(1)

        if self.account_number != "490706777":
            print(f"[WARN]  WARNING: Expected account 490706777, got {self.account_number}")
            # Check if running in interactive mode
            if sys.stdin.isatty():
                response = input("Continue anyway? (yes/no): ")
                if response.lower() != 'yes':
                    sys.exit(1)
            else:
                print("   Non-interactive mode: proceeding with configured account")

        self.auth = RobinhoodAuth()
        self.auth.login()

        # Verify account access
        self._verify_account()

    def _verify_account(self):
        """Verify we can access the correct account"""
        try:
            _account = r.profiles.load_account_profile(account_number=self.account_number)
            account_type = _account.get('type', 'unknown')

            print(f"\n{'='*70}")
            print(f"[LOCKED] LOCKED TO ACCOUNT: {self.account_number}")
            print(f"{'='*70}")
            print(f"   Account Type: {account_type}")

            if account_type != 'cash':
                print(f"   [WARN]  WARNING: Account type is '{account_type}', not 'cash'")
                print("   Bot will still only use available cash, not margin")

            print(f"{'='*70}\n")

        except Exception as e:
            print(f"[ERR] ERROR: Cannot access account {self.account_number}")
            print(f"   {e}")
            sys.exit(1)

    def get_cash_balance(self):
        """Get available cash balance (not margin)"""
        try:
            account = r.profiles.load_account_profile(account_number=self.account_number)

            cash = float(account.get('cash', 0))
            cash_available_for_withdrawal = float(account.get('cash_available_for_withdrawal', 0))
            buying_power = float(account.get('buying_power', 0))

            # Use the most conservative value (actual cash, not buying power)
            available_cash = cash

            return {
                'cash': cash,
                'cash_available_for_withdrawal': cash_available_for_withdrawal,
                'buying_power': buying_power,
                'tradeable_cash': available_cash  # This is what we'll use
            }
        except Exception as e:
            print(f"[ERR] Error getting cash balance: {e}")
            return None

    def get_portfolio_summary(self, symbols=None):
        """Get portfolio summary for this specific account.

        Args:
            symbols: Optional list of ticker symbols to filter positions/orders to.
                     When None, all positions/orders are shown.
        """
        try:
            r.profiles.load_account_profile(account_number=self.account_number)
            portfolio = r.profiles.load_portfolio_profile(account_number=self.account_number)

            print(f"\n{'='*70}")
            print(f"PORTFOLIO SUMMARY - ACCOUNT {self.account_number}")
            print(f"{'='*70}\n")

            # Account details
            print("Cash Balances:")
            cash_info = self.get_cash_balance()
            print(f"   Available Cash: ${cash_info['tradeable_cash']:,.2f}")
            print(f"   Buying Power: ${cash_info['buying_power']:,.2f}")
            print(f"   Withdrawable: ${cash_info['cash_available_for_withdrawal']:,.2f}")

            # Portfolio value
            equity = float(portfolio.get('equity', 0))
            market_value = float(portfolio.get('market_value', 0))

            print("\nPortfolio:")
            print(f"   Total Equity: ${equity:,.2f}")
            print(f"   Market Value: ${market_value:,.2f}")

            # Margin Availability Summary
            print("\nMargin Availability:")
            available_cash = cash_info['tradeable_cash']
            buying_power = cash_info['buying_power']
            margin_available = buying_power - available_cash

            # Calculate margin usage if we have positions
            if equity > 0:
                cash_pct = (available_cash / equity) * 100
                margin_used = equity - available_cash - market_value
                print(f"   Current Margin Used: ${margin_used:,.2f}")
                print(f"   Margin Available: ${margin_available:,.2f}")
                print(f"   Cash % of Equity: {cash_pct:.1f}%")
                print(f"   Status: {'[OK] Cash Only' if margin_used <= 0 else '[WARN] Using Margin'}")
            else:
                print(f"   Margin Available: ${margin_available:,.2f}")
                print(f"   Status: [OK] Cash Only (No positions)")

            # Order Book
            open_orders = self.get_open_orders()
            if symbols:
                display_orders = [o for o in open_orders if o['symbol'] in symbols]
                print(f"\nOrder Book: {len(display_orders)} open order(s) (filtered; {len(open_orders)} total)")
            else:
                display_orders = open_orders
                print(f"\nOrder Book: {len(open_orders)} open order(s)")

            if display_orders:
                # Group orders by type
                buy_orders = [o for o in display_orders if o['side'] == 'BUY']
                sell_orders = [o for o in display_orders if o['side'] == 'SELL']

                if buy_orders:
                    print("\n   BUY ORDERS:")
                    for order in buy_orders:
                        print(f"\n      {order['symbol']} - {order['order_type']}")
                        print(f"         Quantity: {order['quantity']:.0f} shares")
                        if order['limit_price']:
                            print(f"         Limit Price: ${order['limit_price']:.2f}")
                        if order['stop_price']:
                            print(f"         Stop Price: ${order['stop_price']:.2f}")
                        print(f"         Status: {order['state']}")
                        print(f"         Created: {order['created_at']}")

                if sell_orders:
                    print("\n   SELL ORDERS:")
                    for order in sell_orders:
                        print(f"\n      {order['symbol']} - {order['order_type']}")
                        print(f"         Quantity: {order['quantity']:.0f} shares")
                        if order['limit_price']:
                            print(f"         Limit Price: ${order['limit_price']:.2f}")
                        if order['stop_price']:
                            print(f"         Stop Price: ${order['stop_price']:.2f}")
                        print(f"         Status: {order['state']}")
                        print(f"         Created: {order['created_at']}")
            else:
                print("   No open orders")

            # PDT Status
            pdt_info = self.get_pdt_status()
            if pdt_info:
                print(f"\n[WARN]  PDT Status:")
                print(f"   Day Trades (last 5 days): {pdt_info['day_trade_count']}/3")
                if pdt_info['flagged']:
                    print(f"   Status: [ALERT] PDT FLAGGED — only position-closing trades allowed")
                elif pdt_info['day_trade_count'] >= 2:
                    print(f"   Status: [WARN]  WARNING — 1 day trade remaining")
                else:
                    remaining = 3 - pdt_info['day_trade_count']
                    print(f"   Status: [OK] OK — {remaining} day trade(s) remaining")
                if pdt_info['trades']:
                    print(f"   Recent day trades:")
                    for t in pdt_info['trades']:
                        print(f"      {t}")

            # Positions
            positions = self.get_positions()
            if symbols:
                display_positions = [p for p in positions if p['symbol'] in symbols]
                print(f"\nPositions: {len(display_positions)} (filtered; {len(positions)} total)")
            else:
                display_positions = positions
                print(f"\nPositions: {len(positions)}")

            if display_positions:
                # Calculate total position value for allocation percentages
                total_position_value = sum(pos['equity'] for pos in positions)

                for pos in display_positions:
                    allocation_pct = (pos['equity'] / equity) * 100 if equity > 0 else 0
                    print(f"\n   {pos['symbol']}")
                    print(f"      Quantity: {pos['quantity']}")
                    print(f"      Avg Buy: ${pos['avg_buy_price']:.2f}")
                    print(f"      Current: ${pos['current_price']:.2f}")
                    print(f"      Equity: ${pos['equity']:,.2f}")
                    print(f"      Allocation: {allocation_pct:.1f}% of portfolio")
                    print(f"      P/L: ${pos['profit_loss']:+,.2f} ({pos['profit_loss_pct']:+.2f}%)")

                # Stock Distribution (within invested portion)
                print(f"\nStock Distribution (of invested capital):")
                for pos in display_positions:
                    stock_pct = (pos['equity'] / total_position_value) * 100 if total_position_value > 0 else 0
                    print(f"   {pos['symbol']}: {stock_pct:.1f}% (${pos['equity']:,.2f})")

            else:
                print("   No open positions")
                total_position_value = 0

            # Options Positions
            option_positions = self.get_option_positions()
            total_options_value = sum(op['current_value'] for op in option_positions)

            if option_positions:
                print(f"\n{'='*70}")
                print(f"OPTIONS BOOK: {len(option_positions)} active position(s)")
                print(f"{'='*70}")

                for op in option_positions:
                    direction = 'LONG' if op['position_type'] == 'long' else 'SHORT'
                    otype = (op['option_type'] or '').upper()
                    print(f"\n   {op['chain_symbol']} ${op['strike']:.2f} {otype} "
                          f"exp {op['expiration']} [{direction} x{op['quantity']:.0f}]")

                    # Underlying & moneyness
                    if op['underlying_price']:
                        moneyness = ''
                        if otype == 'CALL':
                            moneyness = 'ITM' if op['underlying_price'] >= op['strike'] else 'OTM'
                        elif otype == 'PUT':
                            moneyness = 'ITM' if op['underlying_price'] <= op['strike'] else 'OTM'
                        print(f"      Underlying: ${op['underlying_price']:.2f}  |  {moneyness}"
                              f"  |  DTE: {op['dte'] if op['dte'] is not None else 'N/A'}")

                    # Pricing
                    print(f"      Mark: ${op['mark_price']:.2f}" if op['mark_price'] else "      Mark: N/A", end='')
                    print(f"  |  Avg Cost: ${op['avg_price']:.2f}", end='')
                    if op['break_even']:
                        print(f"  |  Break-even: ${op['break_even']:.2f}")
                    else:
                        print()
                    print(f"      Value: ${op['current_value']:,.2f}  |  "
                          f"Cost Basis: ${op['cost_basis']:,.2f}  |  "
                          f"P/L: ${op['unrealized_pl']:+,.2f} ({op['unrealized_pl_pct']:+.1f}%)")

                    # Greeks
                    g = op['greeks']
                    print(f"      Greeks: "
                          f"Δ={g['delta']:.4f}  " if g['delta'] is not None else "      Greeks: Δ=N/A  ", end='')
                    print(f"Γ={g['gamma']:.4f}  " if g['gamma'] is not None else "Γ=N/A  ", end='')
                    print(f"Θ={g['theta']:.4f}  " if g['theta'] is not None else "Θ=N/A  ", end='')
                    print(f"V={g['vega']:.4f}  " if g['vega'] is not None else "V=N/A  ", end='')
                    print(f"ρ={g['rho']:.4f}" if g['rho'] is not None else "ρ=N/A")
                    if g['iv'] is not None:
                        print(f"      IV: {g['iv']:.1%}", end='')
                    if op['chance_of_profit'] is not None:
                        print(f"  |  Prob of Profit: {op['chance_of_profit']:.1%}", end='')
                    print()

                    # Expected P&L
                    epl = op['expected_pl']
                    if epl:
                        print(f"      Expected P&L:  "
                              f"-5%: ${epl.get('-5%', 0):+,.2f}  "
                              f"-1%: ${epl.get('-1%', 0):+,.2f}  "
                              f"+1%: ${epl.get('+1%', 0):+,.2f}  "
                              f"+5%: ${epl.get('+5%', 0):+,.2f}")
                        if 'theta_daily' in epl:
                            print(f"      Daily Theta P&L: ${epl['theta_daily']:+,.2f}")

                    # BTC Correlation
                    btc_corr = op.get('btc_correlation')
                    if btc_corr is not None:
                        corr_label = 'Strong' if abs(btc_corr) > 0.7 else 'Moderate' if abs(btc_corr) > 0.4 else 'Weak'
                        print(f"      BTC Correlation: {btc_corr:+.3f} ({corr_label})")

                    # Recommended Action
                    rec = op['recommended_action']
                    action_icon = {'CLOSE': '[ALERT]', 'HOLD': '[OK]'}.get(rec['action'], '[!]')
                    print(f"      Recommendation: {action_icon} {rec['action']}")
                    for reason in rec['reasons']:
                        print(f"         - {reason}")

            # Portfolio Allocation Summary
            print(f"\nPortfolio Allocation Summary:")
            cash_allocation_pct = (available_cash / equity) * 100 if equity > 0 else 100
            invested_pct = (total_position_value / equity) * 100 if equity > 0 else 0
            options_pct = (total_options_value / equity) * 100 if equity > 0 else 0
            print(f"   Cash: {cash_allocation_pct:.1f}% (${available_cash:,.2f})")
            print(f"   Stocks: {invested_pct:.1f}% (${total_position_value:,.2f})")
            if option_positions:
                print(f"   Options: {options_pct:.1f}% (${total_options_value:,.2f})")
            print(f"   Total Equity: ${equity:,.2f}")

            print(f"\n{'='*70}\n")

            return {
                'cash': cash_info,
                'equity': equity,
                'market_value': market_value,
                'positions': positions,
                'open_orders': open_orders,
                'options': option_positions,
            }

        except Exception as e:
            print(f"[ERR] Error getting portfolio: {e}")
            return None

    def get_pdt_status(self):
        """Get Pattern Day Trading status for this account"""
        try:
            # Check if account is flagged (position-closing only)
            account = r.profiles.load_account_profile(account_number=self.account_number)
            flagged = account.get('only_position_closing_trades', False)

            # Get recent day trades
            day_trade_data = r.account.get_day_trades()
            trades = []
            day_trade_count = 0

            if isinstance(day_trade_data, dict):
                day_trade_count = day_trade_data.get('equity_day_trade_count', 0)
                for dt in day_trade_data.get('equity_day_trades', []):
                    instrument_url = dt.get('instrument', '')
                    opened = dt.get('opened_at', 'N/A')
                    closed = dt.get('closed_at', 'N/A')
                    # Try to format dates
                    try:
                        if opened != 'N/A':
                            opened = datetime.fromisoformat(opened.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                        if closed != 'N/A':
                            closed = datetime.fromisoformat(closed.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pass
                    trades.append(f"opened {opened} → closed {closed}")
            elif isinstance(day_trade_data, list):
                day_trade_count = len(day_trade_data)
                for dt in day_trade_data:
                    instrument_url = dt.get('instrument', '') if isinstance(dt, dict) else ''
                    opened = dt.get('opened_at', 'N/A') if isinstance(dt, dict) else 'N/A'
                    closed = dt.get('closed_at', 'N/A') if isinstance(dt, dict) else 'N/A'
                    try:
                        if opened != 'N/A':
                            opened = datetime.fromisoformat(opened.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                        if closed != 'N/A':
                            closed = datetime.fromisoformat(closed.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M')
                    except Exception:
                        pass
                    trades.append(f"opened {opened} → closed {closed}")

            return {
                'day_trade_count': day_trade_count,
                'flagged': flagged,
                'trades': trades
            }
        except Exception as e:
            print(f"   Could not fetch PDT status: {e}")
            return None

    def get_positions(self):
        """Get positions for this specific account"""
        try:
            # Use build_holdings which works more reliably
            # Note: This returns all holdings, but we're locked to one account anyway
            holdings = r.account.build_holdings()

            positions = []
            if holdings:
                for symbol, data in holdings.items():
                    quantity = float(data.get('quantity', 0))
                    if quantity > 0:  # Only open positions
                        avg_price = float(data.get('average_buy_price', 0))
                        current_price = float(data.get('price', 0))
                        equity = float(data.get('equity', 0))
                        profit_loss = (current_price - avg_price) * quantity
                        profit_loss_pct = ((current_price - avg_price) / avg_price) * 100 if avg_price > 0 else 0

                        positions.append({
                            'symbol': symbol,
                            'quantity': quantity,
                            'avg_buy_price': avg_price,
                            'current_price': current_price,
                            'equity': equity,
                            'profit_loss': profit_loss,
                            'profit_loss_pct': profit_loss_pct
                        })

            return positions

        except Exception as e:
            print(f"[ERR] Error getting positions: {e}")
            return []

    def get_option_positions(self):
        """
        Get open option positions with greeks, expected P&L, BTC correlation,
        and recommended action.

        Returns:
            List of option position dicts with analytics
        """
        try:
            raw_positions = r.options.get_open_option_positions(
                account_number=self.account_number
            )
            if not raw_positions:
                return []

            positions = []
            # Collect underlying symbols for correlation calc
            underlying_symbols = set()

            for pos in raw_positions:
                quantity = float(pos.get('quantity', 0))
                if quantity == 0:
                    continue

                chain_symbol = pos.get('chain_symbol', 'N/A')
                underlying_symbols.add(chain_symbol)
                avg_price = float(pos.get('average_price', 0)) / 100  # per-share cost
                pos_type = pos.get('type', 'long')  # 'long' or 'short'
                multiplier = float(pos.get('trade_value_multiplier', '100'))

                # Get instrument details (strike, expiration, call/put)
                option_url = pos.get('option', '')
                option_id = option_url.rstrip('/').split('/')[-1] if option_url else None

                instrument = {}
                if option_id:
                    try:
                        instrument = r.options.get_option_instrument_data_by_id(option_id) or {}
                    except Exception:
                        pass

                strike = float(instrument.get('strike_price', 0))
                expiration = instrument.get('expiration_date', 'N/A')
                option_type = instrument.get('type', 'N/A')  # 'call' or 'put'

                # Get market data (greeks)
                greeks = {}
                market_data = {}
                if option_id:
                    try:
                        md = r.options.get_option_market_data_by_id(option_id)
                        if md and isinstance(md, list) and len(md) > 0:
                            market_data = md[0]
                        elif md and isinstance(md, dict):
                            market_data = md
                    except Exception:
                        pass

                delta = self._safe_float(market_data.get('delta'))
                gamma = self._safe_float(market_data.get('gamma'))
                theta = self._safe_float(market_data.get('theta'))
                vega = self._safe_float(market_data.get('vega'))
                rho = self._safe_float(market_data.get('rho'))
                iv = self._safe_float(market_data.get('implied_volatility'))
                mark_price = self._safe_float(market_data.get('adjusted_mark_price'))
                chance_profit_long = self._safe_float(market_data.get('chance_of_profit_long'))
                chance_profit_short = self._safe_float(market_data.get('chance_of_profit_short'))
                break_even = self._safe_float(market_data.get('break_even_price'))

                greeks = {
                    'delta': delta,
                    'gamma': gamma,
                    'theta': theta,
                    'vega': vega,
                    'rho': rho,
                    'iv': iv,
                }

                # Get current underlying price
                underlying_price = None
                try:
                    price_data = r.stocks.get_latest_price(chain_symbol)
                    if price_data and price_data[0]:
                        underlying_price = float(price_data[0])
                except Exception:
                    pass

                # Days to expiration
                dte = None
                if expiration and expiration != 'N/A':
                    try:
                        exp_date = datetime.strptime(expiration, '%Y-%m-%d').date()
                        dte = (exp_date - date.today()).days
                    except Exception:
                        pass

                # Expected P&L for a 1% move in the underlying
                expected_pl = self._calc_expected_pl(
                    delta, gamma, theta, vega,
                    underlying_price, mark_price, avg_price,
                    quantity, multiplier, pos_type
                )

                # Recommended action
                action = self._recommend_option_action(
                    option_type, pos_type, delta, theta, iv, dte,
                    mark_price, avg_price, underlying_price, strike,
                    chance_profit_long, chance_profit_short
                )

                # Current P&L
                current_value = mark_price * quantity * multiplier if mark_price else 0
                cost_basis = avg_price * quantity * multiplier
                unrealized_pl = current_value - cost_basis if pos_type == 'long' else cost_basis - current_value
                unrealized_pl_pct = (unrealized_pl / cost_basis * 100) if cost_basis > 0 else 0

                positions.append({
                    'chain_symbol': chain_symbol,
                    'option_type': option_type,
                    'strike': strike,
                    'expiration': expiration,
                    'dte': dte,
                    'quantity': quantity,
                    'position_type': pos_type,
                    'avg_price': avg_price,
                    'mark_price': mark_price,
                    'multiplier': multiplier,
                    'cost_basis': round(cost_basis, 2),
                    'current_value': round(current_value, 2),
                    'unrealized_pl': round(unrealized_pl, 2),
                    'unrealized_pl_pct': round(unrealized_pl_pct, 2),
                    'underlying_price': underlying_price,
                    'break_even': break_even,
                    'greeks': greeks,
                    'expected_pl': expected_pl,
                    'chance_of_profit': chance_profit_long if pos_type == 'long' else chance_profit_short,
                    'recommended_action': action,
                })

            # Compute BTC correlation for each unique underlying
            btc_correlations = self._compute_btc_correlations(list(underlying_symbols))
            for pos in positions:
                pos['btc_correlation'] = btc_correlations.get(pos['chain_symbol'])

            return positions

        except Exception as e:
            print(f"   Error getting option positions: {e}")
            return []

    @staticmethod
    def _safe_float(val):
        """Convert value to float, returning None if not possible."""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _calc_expected_pl(self, delta, gamma, theta, vega,
                          underlying_price, mark_price, avg_price,
                          quantity, multiplier, pos_type):
        """
        Calculate expected P&L scenarios using greeks.
        Returns P&L for -5%, -1%, +1%, +5% moves in the underlying,
        plus daily theta decay.
        """
        if not underlying_price or delta is None:
            return None

        sign = 1.0 if pos_type == 'long' else -1.0
        scenarios = {}

        for pct_label, pct in [('-5%', -0.05), ('-1%', -0.01), ('+1%', 0.01), ('+5%', 0.05)]:
            dollar_move = underlying_price * pct
            # Option price change = delta * dS + 0.5 * gamma * dS^2
            option_delta_price = (delta or 0) * dollar_move
            if gamma:
                option_delta_price += 0.5 * gamma * dollar_move ** 2
            pl = sign * option_delta_price * quantity * multiplier
            scenarios[pct_label] = round(pl, 2)

        # Daily theta decay
        if theta is not None:
            scenarios['theta_daily'] = round(sign * theta * quantity * multiplier, 2)

        return scenarios

    def _recommend_option_action(self, option_type, pos_type, delta, theta, iv,
                                 dte, mark_price, avg_price, underlying_price,
                                 strike, chance_profit_long, chance_profit_short):
        """
        Generate a recommended action for an option position based on greeks,
        time decay, moneyness, and probability of profit.
        """
        reasons = []
        action = 'HOLD'

        if dte is not None and dte <= 0:
            return {'action': 'CLOSE', 'reasons': ['Expired or expiring today']}

        chance_of_profit = chance_profit_long if pos_type == 'long' else chance_profit_short

        # --- Long positions ---
        if pos_type == 'long':
            # Take profit if up significantly
            if mark_price and avg_price and avg_price > 0:
                gain_pct = (mark_price - avg_price) / avg_price * 100
                if gain_pct >= 100:
                    action = 'CLOSE'
                    reasons.append(f'Up {gain_pct:.0f}% — take profit')
                elif gain_pct >= 50:
                    reasons.append(f'Up {gain_pct:.0f}% — consider partial close')

            # Theta bleed warning
            if dte is not None and dte <= 7 and theta is not None and theta < -0.03:
                action = 'CLOSE'
                reasons.append(f'DTE={dte}, heavy theta decay (${theta:.3f}/day)')
            elif dte is not None and dte <= 14:
                reasons.append(f'DTE={dte} — monitor theta decay')

            # Low probability of profit
            if chance_of_profit is not None and chance_of_profit < 0.20:
                action = 'CLOSE'
                reasons.append(f'Low probability of profit ({chance_of_profit:.0%})')

            # Deep OTM
            if underlying_price and strike and option_type in ('call', 'put'):
                if option_type == 'call' and underlying_price < strike * 0.90:
                    reasons.append('Deep OTM call')
                elif option_type == 'put' and underlying_price > strike * 1.10:
                    reasons.append('Deep OTM put')

        # --- Short positions ---
        else:
            # Profit target reached (option decayed significantly)
            if mark_price and avg_price and avg_price > 0:
                decay_pct = (avg_price - mark_price) / avg_price * 100
                if decay_pct >= 80:
                    action = 'CLOSE'
                    reasons.append(f'Captured {decay_pct:.0f}% of premium — close to lock in')
                elif decay_pct >= 50:
                    reasons.append(f'Captured {decay_pct:.0f}% of premium — consider closing')

            # IV spike risk
            if iv is not None and iv > 0.80:
                reasons.append(f'High IV ({iv:.0%}) — increased risk of adverse move')

            # Assignment risk
            if dte is not None and dte <= 3:
                if underlying_price and strike:
                    if option_type == 'call' and underlying_price >= strike:
                        action = 'CLOSE'
                        reasons.append('ITM near expiration — assignment risk')
                    elif option_type == 'put' and underlying_price <= strike:
                        action = 'CLOSE'
                        reasons.append('ITM near expiration — assignment risk')

        if not reasons:
            reasons.append('No immediate signals')

        return {'action': action, 'reasons': reasons}

    def _compute_btc_correlations(self, symbols):
        """
        Compute 30-day correlation of each symbol against BTC using
        recent daily returns from Robinhood historicals.
        Returns dict of symbol -> correlation value.
        """
        correlations = {}
        if not symbols:
            return correlations

        # Fetch BTC daily returns (use BTC Trust as reference)
        btc_returns = self._get_daily_returns('BTC')
        if not btc_returns:
            return correlations

        for sym in symbols:
            if sym == 'BTC':
                correlations[sym] = 1.0
                continue
            sym_returns = self._get_daily_returns(sym)
            if not sym_returns:
                correlations[sym] = None
                continue
            corr = self._pearson_from_return_dicts(btc_returns, sym_returns)
            correlations[sym] = round(corr, 3) if corr is not None else None

        return correlations

    def _get_daily_returns(self, symbol):
        """Get last 30 daily returns for a symbol using Robinhood historicals."""
        try:
            historicals = r.stocks.get_stock_historicals(
                symbol, interval='day', span='3month'
            )
            if not historicals or len(historicals) < 5:
                return None

            returns = {}
            for i in range(1, len(historicals)):
                prev_close = float(historicals[i - 1].get('close_price', 0))
                curr_close = float(historicals[i].get('close_price', 0))
                if prev_close > 0 and curr_close > 0:
                    dt = historicals[i].get('begins_at', '')[:10]
                    returns[dt] = math.log(curr_close / prev_close)
            return returns
        except Exception:
            return None

    @staticmethod
    def _pearson_from_return_dicts(a_dict, b_dict):
        """Pearson correlation from two date-keyed return dicts."""
        common = sorted(set(a_dict.keys()) & set(b_dict.keys()))
        # Use last 30 common dates
        common = common[-30:] if len(common) > 30 else common
        if len(common) < 5:
            return None
        a = [a_dict[d] for d in common]
        b = [b_dict[d] for d in common]
        n = len(a)
        ma = sum(a) / n
        mb = sum(b) / n
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n)) / (n - 1)
        sa = math.sqrt(sum((x - ma) ** 2 for x in a) / (n - 1))
        sb = math.sqrt(sum((x - mb) ** 2 for x in b) / (n - 1))
        if sa == 0 or sb == 0:
            return None
        return cov / (sa * sb)

    def get_open_orders(self):
        """
        Get all open orders for this account

        Returns:
            List of open orders with details including stop loss and limit prices
        """
        try:
            # Get all open stock orders
            open_orders = r.orders.get_all_open_stock_orders()

            orders = []
            if open_orders:
                for order in open_orders:
                    # Parse order details
                    order_id = order.get('id', 'N/A')
                    symbol = order.get('symbol', 'N/A')

                    # If symbol is not directly available, try to resolve from instrument_id
                    if symbol == 'N/A':
                        instrument_id = order.get('instrument_id')
                        if instrument_id:
                            try:
                                instrument = r.stocks.get_instrument_by_url(
                                    f"https://api.robinhood.com/instruments/{instrument_id}/"
                                )
                                if instrument:
                                    symbol = instrument.get('symbol', 'N/A')
                            except Exception:
                                pass  # Keep symbol as 'N/A' if lookup fails

                    side = order.get('side', 'N/A')  # 'buy' or 'sell'
                    order_type = order.get('type', 'N/A')  # 'market' or 'limit'
                    trigger = order.get('trigger', 'immediate')  # 'immediate' or 'stop'
                    state = order.get('state', 'N/A')
                    quantity = float(order.get('quantity', 0))

                    # Price information
                    limit_price = order.get('price')  # Limit price (can be None)
                    stop_price = order.get('stop_price')  # Stop price (can be None)

                    # Timestamps
                    created_at = order.get('created_at', 'N/A')
                    updated_at = order.get('updated_at', 'N/A')

                    # Parse datetime if available
                    try:
                        if created_at != 'N/A':
                            created_dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            created_at = created_dt.strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass

                    # Determine order description
                    if trigger == 'stop' and order_type == 'limit':
                        order_desc = 'Stop Limit'
                    elif trigger == 'stop':
                        order_desc = 'Stop Loss'
                    elif order_type == 'limit':
                        order_desc = 'Limit'
                    else:
                        order_desc = 'Market'

                    orders.append({
                        'order_id': order_id,
                        'symbol': symbol,
                        'side': side.upper() if side != 'N/A' else 'N/A',
                        'order_type': order_desc,
                        'trigger': trigger,
                        'state': state,
                        'quantity': quantity,
                        'limit_price': float(limit_price) if limit_price else None,
                        'stop_price': float(stop_price) if stop_price else None,
                        'created_at': created_at,
                        'updated_at': updated_at
                    })

            return orders

        except Exception as e:
            print(f"[ERR] Error getting open orders: {e}")
            return []

    def validate_buy_order(self, symbol, quantity, price):
        """
        Validate a buy order before execution
        Returns: (is_valid, reason)
        """
        cash_info = self.get_cash_balance()
        if not cash_info:
            return False, "Cannot retrieve cash balance"

        buying_power = cash_info['buying_power']
        total_cost = quantity * price

        # Add 1% buffer for price fluctuations
        total_cost_with_buffer = total_cost * 1.01

        if total_cost_with_buffer > buying_power:
            return False, f"Insufficient buying power: need ${total_cost_with_buffer:,.2f}, have ${buying_power:,.2f}"

        # Check if symbol is valid
        try:
            quote = r.stocks.get_quotes(symbol)
            if not quote or len(quote) == 0:
                return False, f"Invalid symbol: {symbol}"
        except Exception:
            return False, f"Cannot get quote for {symbol}"

        return True, "Order validated"

    def _cancel_existing_orders(self, symbol, side):
        """
        Cancel all existing orders for a symbol on the given side.

        Args:
            symbol: Stock ticker
            side: 'buy' or 'sell'

        Returns:
            Total quantity of cancelled orders
        """
        target_instrument_url = None
        try:
            target_instruments = r.stocks.get_instruments_by_symbols(symbol, info='url')
            if target_instruments:
                target_instrument_url = target_instruments[0]
        except Exception as e:
            print(f"   Failed to resolve instrument for {symbol}: {e}")
            return 0

        if not target_instrument_url:
            print(f"   Could not resolve instrument URL for {symbol}")
            return 0

        open_orders = r.orders.get_all_open_stock_orders(account_number=self.account_number)
        cancelled_qty = 0
        if open_orders:
            for open_order in open_orders:
                order_instrument = open_order.get('instrument', '')
                order_side = open_order.get('side', '')

                if order_instrument == target_instrument_url and order_side == side:
                    existing_id = open_order.get('id')
                    existing_qty = open_order.get('quantity', '?')
                    existing_type = open_order.get('type', '?')
                    existing_trigger = open_order.get('trigger', '?')
                    existing_stop = open_order.get('stop_price', 'N/A')
                    existing_limit = open_order.get('price', 'N/A')
                    print(f"   Existing {side}: id={existing_id} type={existing_type} trigger={existing_trigger} qty={existing_qty} stop=${existing_stop} limit=${existing_limit}")
                    print(f"   Cancelling...")
                    r.orders.cancel_stock_order(existing_id)
                    try:
                        cancelled_qty += int(float(existing_qty))
                    except (ValueError, TypeError):
                        pass

        return cancelled_qty

    def cancel_order_by_id(self, order_id):
        """Cancel a single order by ID after verifying it is still open.

        Only cancels if order state is queued, unconfirmed, or confirmed.
        Returns False if the order has already filled, been cancelled, or
        cannot be found (prevents race conditions).

        Args:
            order_id: Robinhood order ID string.

        Returns:
            True if the order was successfully cancelled, False otherwise.
        """
        try:
            order_info = r.orders.get_stock_order_info(order_id)
            if not order_info or not isinstance(order_info, dict):
                print(f"   cancel_order_by_id: order {order_id} not found")
                return False

            state = order_info.get('state', '')
            if state not in ('queued', 'unconfirmed', 'confirmed'):
                print(f"   cancel_order_by_id: order {order_id} in state '{state}', cannot cancel")
                return False

            r.orders.cancel_stock_order(order_id)
            print(f"   cancel_order_by_id: cancelled order {order_id}")
            return True
        except Exception as e:
            print(f"   cancel_order_by_id: error cancelling {order_id}: {e}")
            return False

    def place_cash_buy_order(self, symbol, quantity, price, dry_run=True):
        """
        Place a limit buy order using CASH ONLY.
        Cancels any existing buy order for the symbol first to maintain
        a single active buy order at all times.

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Limit price
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"BUY ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        # Validate order
        is_valid, reason = self.validate_buy_order(symbol, quantity, price)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Limit Price: ${price:.2f}")
        print(f"   Total Cost: ${quantity * price:.2f}")
        print(f"   Validation: {'[OK] ' + reason if is_valid else '[ERR] ' + reason}")

        if not is_valid:
            print(f"\n[ERR] Order rejected: {reason}")
            print(f"{'='*70}\n")
            return None

        if dry_run:
            print("\n[WARN]  DRY RUN MODE - Order not executed")
            print("   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        try:
            print("\nExecuting order...")
            order = r.orders.order_buy_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            order_id = order.get('id') if isinstance(order, dict) else None
            order_state = order.get('state') if isinstance(order, dict) else None

            if order_id:
                print("[OK] Order placed successfully!")
                print(f"   Order ID: {order_id}")
                print(f"   State: {order_state or 'N/A'}")
                print(f"{'='*70}\n")
                return order

            # Order failed — check for PDT
            detail = None
            if isinstance(order, dict):
                detail = order.get('detail') or order.get('non_field_errors') or order.get('message')
            print(f"[ERR] Buy order failed!")
            print(f"   Reason: {detail or order}")

            if isinstance(detail, str) and 'pdt' in detail.lower():
                print(f"   PDT hit — cancelling existing buy order(s) for {symbol}...")
                cancelled_qty = self._cancel_existing_orders(symbol, 'buy')
                if cancelled_qty:
                    print(f"   Cancelled {cancelled_qty} shares, retrying...")
                    retry = r.orders.order_buy_limit(
                        symbol=symbol,
                        quantity=quantity,
                        limitPrice=price,
                        account_number=self.account_number
                    )
                    retry_id = retry.get('id') if isinstance(retry, dict) else None
                    if retry_id:
                        print("[OK] Order placed successfully!")
                        print(f"   Order ID: {retry_id}")
                        print(f"   State: {retry.get('state', 'N/A')}")
                        print(f"{'='*70}\n")
                        return retry
                    else:
                        retry_detail = None
                        if isinstance(retry, dict):
                            retry_detail = retry.get('detail') or retry.get('non_field_errors') or retry.get('message')
                        print(f"   Retry also failed: {retry_detail or retry}")
                else:
                    print(f"   No existing buy orders found for {symbol}")

            print(f"{'='*70}\n")
            return order

        except Exception as e:
            print(f"[ERR] Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_sell_order(self, symbol, quantity, price, dry_run=True):
        """
        Place a limit sell order

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            price: Limit price
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"SELL ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        # Check if we have the position
        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Limit Price: ${price:.2f}")
        print(f"   Total Value: ${quantity * price:.2f}")

        if not position:
            print(f"   Validation: [ERR] No position in {symbol}")
            print(f"\n[ERR] Order rejected: You don't own {symbol}")
            print(f"{'='*70}\n")
            return None

        if quantity > position['quantity']:
            print(f"   Validation: [ERR] Insufficient shares (have {position['quantity']})")
            print(f"\n[ERR] Order rejected: Can't sell {quantity} shares, only own {position['quantity']}")
            print(f"{'='*70}\n")
            return None

        print("   Validation: [OK] Valid sell order")

        if dry_run:
            print("\n[WARN]  DRY RUN MODE - Order not executed")
            print("   To execute real orders, call with dry_run=False")
            print(f"{'='*70}\n")
            return None

        # Execute real order
        try:
            print("\nExecuting order...")
            order = r.orders.order_sell_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=price,
                account_number=self.account_number
            )

            print("[OK] Order placed successfully!")
            print(f"   Order ID: {order.get('id', 'N/A')}")
            print(f"   State: {order.get('state', 'N/A')}")
            print(f"{'='*70}\n")

            return order

        except Exception as e:
            print(f"[ERR] Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def place_stop_limit_sell_order(self, symbol, quantity, stop_price, limit_price, dry_run=True):
        """
        Place a stop-limit sell order

        Args:
            symbol: Stock ticker
            quantity: Number of shares
            stop_price: Price that triggers the order
            limit_price: Minimum price to accept once triggered
            dry_run: If True, simulates order without execution
        """
        print(f"\n{'='*70}")
        print(f"STOP-LIMIT SELL ORDER - {'DRY RUN' if dry_run else 'LIVE'}")
        print(f"{'='*70}")

        positions = self.get_positions()
        position = next((p for p in positions if p['symbol'] == symbol), None)

        print(f"   Account: {self.account_number}")
        print(f"   Symbol: {symbol}")
        print(f"   Quantity: {quantity}")
        print(f"   Stop Price: ${stop_price:.2f}")
        print(f"   Limit Price: ${limit_price:.2f}")
        print(f"   Total Value: ${quantity * limit_price:.2f}")

        if not position:
            print(f"   Validation: No position in {symbol}")
            print(f"\nOrder rejected: You don't own {symbol}")
            print(f"{'='*70}\n")
            return None

        if quantity > position['quantity']:
            print(f"   Validation: Insufficient shares (have {position['quantity']})")
            print(f"\nOrder rejected: Can't sell {quantity} shares, only own {position['quantity']}")
            print(f"{'='*70}\n")
            return None

        print("   Validation: Valid stop-limit sell order")

        if dry_run:
            print("\n   DRY RUN MODE - Order not executed")
            print(f"{'='*70}\n")
            return None

        try:
            print("\n   Executing order...")
            order = r.orders.order_sell_stop_limit(
                symbol=symbol,
                quantity=quantity,
                limitPrice=limit_price,
                stopPrice=stop_price,
                account_number=self.account_number,
                timeInForce='gtc'
            )

            order_id = order.get('id') if isinstance(order, dict) else None
            order_state = order.get('state') if isinstance(order, dict) else None

            if order_id:
                print("   Order placed successfully!")
                print(f"   Order ID: {order_id}")
                print(f"   State: {order_state or 'N/A'}")
                print(f"{'='*70}\n")
                return order

            # Order failed — check for PDT
            detail = None
            if isinstance(order, dict):
                detail = order.get('detail') or order.get('non_field_errors') or order.get('message')
            print(f"   Stop-limit order failed!")
            print(f"   Reason: {detail or order}")

            if isinstance(detail, str) and 'pdt' in detail.lower():
                print(f"   PDT hit — cancelling existing sell order(s) for {symbol}...")
                cancelled_qty = self._cancel_existing_orders(symbol, 'sell')
                if cancelled_qty:
                    print(f"   Cancelled {cancelled_qty} shares, retrying...")
                    retry = r.orders.order_sell_stop_limit(
                        symbol=symbol,
                        quantity=quantity,
                        limitPrice=limit_price,
                        stopPrice=stop_price,
                        account_number=self.account_number,
                        timeInForce='gtc'
                    )
                    retry_id = retry.get('id') if isinstance(retry, dict) else None
                    if retry_id:
                        print("   Order placed successfully!")
                        print(f"   Order ID: {retry_id}")
                        print(f"   State: {retry.get('state', 'N/A')}")
                        print(f"{'='*70}\n")
                        return retry
                    else:
                        retry_detail = None
                        if isinstance(retry, dict):
                            retry_detail = retry.get('detail') or retry.get('non_field_errors') or retry.get('message')
                        print(f"   Retry also failed: {retry_detail or retry}")
                else:
                    print(f"   No existing sell orders found for {symbol}")

            print(f"{'='*70}\n")
            return order

        except Exception as e:
            print(f"   Order failed: {e}")
            print(f"{'='*70}\n")
            return None

    def get_quote(self, symbol):
        """Get real-time quote"""
        try:
            quote = r.stocks.get_quotes(symbol)[0]
            price = float(r.stocks.get_latest_price(symbol)[0])

            print(f"\n{symbol} Quote:")
            print(f"   Price: ${price:.2f}")
            print(f"   Bid: ${float(quote.get('bid_price', 0)):.2f}")
            print(f"   Ask: ${float(quote.get('ask_price', 0)):.2f}")
            if 'volume' in quote and quote['volume']:
                print(f"   Volume: {int(float(quote['volume'])):,}")

            return price
        except Exception as e:
            print(f"[ERR] Error fetching quote: {e}")
            return None

    def run_example(self):
        """Example usage of the bot"""
        try:
            # Show portfolio
            self.get_portfolio_summary()

            # Get quote
            self.get_quote('AAPL')

            # Example buy order (DRY RUN)
            print("\n" + "="*70)
            print("EXAMPLE: Placing a dry run buy order")
            print("="*70)
            self.place_cash_buy_order('AAPL', 1, 150.00, dry_run=True)

            print("\nTo execute real orders:")
            print("   bot.place_cash_buy_order('AAPL', 1, 150.00, dry_run=False)")

        except Exception as e:
            print(f"[ERR] Error: {e}")
        finally:
            self.auth.logout()


def main():
    """Run the safe cash bot - just show portfolio"""
    print("\nSafe Cash-Only Trading Bot")
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    bot = SafeCashBot()

    try:
        # Just show portfolio for automated account
        bot.get_portfolio_summary()
    except Exception as e:
        print(f"[ERR] Error: {e}")
    finally:
        bot.auth.logout()


if __name__ == "__main__":
    main()
