"""
Main Trading System Orchestrator
Coordinates market data, strategy execution, and order management
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict
import time

import requests

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_system.data_providers.twelve_data import TwelveDataProvider  # noqa: E402
from trading_system.utils.metrics import MetricsCalculator  # noqa: E402
from trading_system.strategies.breakout_strategy import BreakoutStrategy  # noqa: E402
from trading_system.strategies.momentum_dca_strategy import MomentumDcaLongStrategy  # noqa: E402
from trading_system.state.state_manager import StateManager  # noqa: E402
from trading_system.state.blob_logger import log_state_to_blob  # noqa: E402
from trading_system.state.redis_store import sync_to_redis  # noqa: E402
from trading_system.market_indicators import fetch_and_write_indicators  # noqa: E402
from trading_system.utils.slack import send_slack_alert  # noqa: E402
from trading_system.entities.OrderType import OrderSide  # noqa: E402
from utils.safe_cash_bot import SafeCashBot  # noqa: E402


class TradingSystem:
    """Main trading system orchestrator"""

    def __init__(self, twelve_data_api_key: str, symbols: List[str],
                 position_size_pct: float = 0.25, dry_run: bool = True,
                 strategy_name: str = 'momentum_dca',
                 verbose: bool = False,
                 dashboard: bool = False,
                 recent_days: int = None):
        """
        Initialize trading system

        Args:
            twelve_data_api_key: Twelve Data API key
            symbols: List of symbols to trade
            position_size_pct: Position size as percentage of portfolio
            dry_run: If True, simulates orders without execution
            strategy_name: Strategy to use ('momentum_dca' or 'breakout')
            verbose: If True, show detailed output (metrics, portfolio, etc.)
            dashboard: If True, fetch market indicators and write dashboard data each cycle
            recent_days: If set, limit backtest to last N daily bars
        """
        self.symbols = symbols
        self.dry_run = dry_run
        self.strategy_name = strategy_name
        self.verbose = verbose
        self.dashboard = dashboard
        self.recent_days = recent_days

        # Initialize components
        self.data_provider = TwelveDataProvider(twelve_data_api_key)
        self.metrics_calculator = MetricsCalculator()
        self.state_manager = StateManager()
        self.trading_bot = SafeCashBot()

        # Initialize execution quality layer
        self.fill_logger = None
        try:
            from trading_system.execution.fill_auditor import FillAuditor
            from trading_system.execution.spread_checker import SpreadChecker
            from trading_system.execution.price_optimizer import PriceOptimizer
            from trading_system.execution.pdt_gate import PDTGate
            from trading_system.execution.fill_logger import FillLogger

            fill_auditor = FillAuditor(
                alpaca_key=os.getenv('ALPACA_API_KEY', ''),
                alpaca_secret=os.getenv('ALPACA_SECRET_KEY', ''),
                twelve_data_provider=self.data_provider,
            )
            spread_checker = SpreadChecker(fill_auditor=fill_auditor)
            price_optimizer = PriceOptimizer()
            pdt_gate = PDTGate(trading_bot=self.trading_bot)
            fill_logger = FillLogger()

            self.trading_bot.init_execution_layer(
                fill_auditor=fill_auditor,
                spread_checker=spread_checker,
                price_optimizer=price_optimizer,
                pdt_gate=pdt_gate,
                fill_logger=fill_logger,
            )

            self.fill_logger = fill_logger
        except Exception as e:
            print(f"  [exec-layer] Execution quality layer init failed (proceeding without): {e}")

        if strategy_name == 'momentum_dca_long':
            self.strategy = MomentumDcaLongStrategy(symbols)
        else:
            self.strategy = BreakoutStrategy(symbols, position_size_pct)

        if self.verbose:
            print(f"\n{'='*70}")
            print("TRADING SYSTEM INITIALIZED")
            print(f"{'='*70}")
            print(f"Strategy: {strategy_name}")
            print(f"Symbols: {', '.join(symbols)}")
            print(f"Mode: {'DRY RUN (Simulation)' if dry_run else 'LIVE TRADING'}")
            print(f"Position Size: {position_size_pct * 100}% per symbol")
            print(f"{'='*70}\n")

    def fetch_market_data(self, symbol: str) -> Dict:
        """
        Fetch market data for a symbol

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with intraday and daily data
        """
        if self.verbose:
            print(f"Fetching market data for {symbol}...")

        # Fetch intraday data (5min intervals, last day)
        intraday_data = self.data_provider.get_intraday_data(
            symbol, interval='5min', outputsize=390
        )

        # Fetch 30-day daily data
        daily_data = self.data_provider.get_daily_data(
            symbol, outputsize=30
        )

        if not intraday_data or not daily_data:
            if self.verbose:
                print(f"  Warning: Incomplete data for {symbol}")

        return {
            'intraday': intraday_data or [],
            'daily': daily_data or []
        }

    def calculate_metrics(self, symbol: str, market_data: Dict) -> Dict:
        """
        Calculate metrics for a symbol

        Args:
            symbol: Stock symbol
            market_data: Market data dictionary

        Returns:
            Calculated metrics
        """
        metrics = self.metrics_calculator.calculate_all_metrics(
            market_data['intraday'],
            market_data['daily']
        )

        # Store metrics in state
        self.state_manager.update_metrics(symbol, metrics)

        return metrics

    def execute_strategy(self, symbol: str, metrics: Dict,
                         open_orders: List[Dict] = None) -> Dict:
        """
        Execute strategy for a symbol

        Args:
            symbol: Stock symbol
            metrics: Calculated metrics
            open_orders: Open orders from broker (used by momentum_dca)

        Returns:
            Signal data
        """
        # Get current position
        positions = self.trading_bot.get_positions()
        current_position = next(
            (p for p in positions if p['symbol'] == symbol),
            None
        )

        # Load broker orders into the symbol's Ticker (all strategies get a Ticker)
        self.state_manager.load_broker_orders(symbol, open_orders or [])
        ticker = self.state_manager.get_ticker(symbol)

        return self.strategy.analyze_symbol(symbol, metrics, current_position, ticker)

    def process_signal(self, symbol: str, signal: Dict, open_orders: list = None):
        """
        Process trading signal and execute orders

        Args:
            symbol: Stock symbol
            signal: Signal data from strategy
            open_orders: Current open orders from broker
        """
        print(self.strategy.format_signal(symbol, signal))

        # Store signal in state
        self.state_manager.set_last_signal(symbol, signal['signal'])

        if not signal['order']:
            return

        # If 2+ orders already on the book, attempt replacement instead of skipping
        symbol_orders = [o for o in (open_orders or []) if o.get('symbol') == symbol]
        if len(symbol_orders) >= 2:
            self._handle_order_replacement(symbol, signal, symbol_orders)
            return

        order = signal['order']

        if order['action'] == 'buy':
            self._execute_buy_order(symbol, order)
        elif order['action'] == 'sell':
            self._execute_sell_order(symbol, order)
        elif order['action'] == 'stop_limit_sell':
            has_paired_buy = signal.get('paired_buy') is not None

            # Cancel ALL existing orders for this side before placing consolidated order
            self._cancel_orders_by_side(symbol, 'SELL', open_orders)
            if has_paired_buy:
                self._cancel_orders_by_side(symbol, 'BUY', open_orders)

            # Use target_qty (full coverage) instead of gap_qty (incremental)
            target_qty = signal.get('target_qty')
            if target_qty:
                order = dict(order)
                order['quantity'] = target_qty

            if has_paired_buy:
                paired_buy = dict(signal['paired_buy'])
                if target_qty:
                    paired_buy['quantity'] = target_qty

                # Default: place buy first, then sell.
                # If first existing order is a buy, place sell first instead.
                sell_first = False
                if open_orders:
                    for o in open_orders:
                        if o['symbol'] == symbol:
                            sell_first = o['side'] == 'BUY'
                            break

                if sell_first:
                    self._execute_stop_limit_sell_order(symbol, order)
                    self._execute_paired_limit_buy(symbol, paired_buy)
                else:
                    self._execute_paired_limit_buy(symbol, paired_buy)
                    self._execute_stop_limit_sell_order(symbol, order)
            else:
                self._execute_stop_limit_sell_order(symbol, order)
        elif order['action'] == 'limit_sell':
            # Cancel existing sells before resubmit
            self._cancel_orders_by_side(symbol, 'SELL', open_orders)
            self._execute_limit_sell_resubmit(symbol, order)

    def _cancel_orders_by_side(self, symbol: str, side: str, open_orders: list) -> tuple:
        """Cancel ALL open orders for symbol on the given side.

        Returns (cancelled_count, total_qty_cancelled).
        """
        cancelled = 0
        qty_cancelled = 0
        for order in (open_orders or []):
            if (order.get('symbol') == symbol
                    and order.get('side') == side):
                order_id = order.get('order_id')
                if order_id and self.trading_bot.cancel_order_by_id(order_id):
                    cancelled += 1
                    qty_cancelled += int(float(order.get('quantity', 0)))
                    print(f"  Cancelled {side} {order_id} qty={int(float(order.get('quantity', 0)))}")
        return cancelled, qty_cancelled

    def _handle_order_replacement(self, symbol: str, signal: Dict, symbol_orders: list):
        """Cancel all orders for the symbol and place a fresh
        stop-limit sell + paired buy at current momentum pricing.

        Ensures exactly 1 sell + 1 buy per lot on the book.

        Steps:
          1. PDT safety check
          2. Cancel ALL existing orders (buys and sells)
          3. Place replacement stop-limit sell + paired buy
        """
        lot_size = getattr(self.strategy, 'lot_size', None)

        # PDT check now handled centrally by PDTGate in SafeCashBot.init_execution_layer()

        # Cancel ALL existing orders for the symbol
        sells_cancelled, _ = self._cancel_orders_by_side(symbol, 'SELL', symbol_orders)
        buys_cancelled, _ = self._cancel_orders_by_side(symbol, 'BUY', symbol_orders)
        print(f"   Order replacement: cancelled {sells_cancelled} sell(s) + {buys_cancelled} buy(s) for {symbol}")

        # Build replacement orders using momentum pricing from the signal
        current_price = signal['order'].get('current_price', 0)
        stop_offset_pct = getattr(self.strategy, 'stop_offset_pct', 0.01)
        buy_offset = getattr(self.strategy, 'buy_offset', 0.20)
        hedge_symbol_map = getattr(self.strategy, 'hedge_symbol_map', {})

        stop_price = round(current_price * (1 - stop_offset_pct), 2)
        limit_price = stop_price
        buy_price = round(stop_price - buy_offset, 2)
        order_symbol = hedge_symbol_map.get(symbol, symbol)
        qty = lot_size

        sell_order = {
            'action': 'stop_limit_sell',
            'symbol': order_symbol,
            'quantity': qty,
            'stop_price': stop_price,
            'limit_price': limit_price,
            'current_price': current_price,
        }
        paired_buy = {
            'action': 'limit_buy',
            'symbol': order_symbol,
            'quantity': qty,
            'price': buy_price,
            'current_price': current_price,
        }

        print(f"   Order replacement: placing sell @ ${stop_price:.2f} + buy @ ${buy_price:.2f}")
        self._execute_stop_limit_sell_order(symbol, sell_order)
        self._execute_paired_limit_buy(symbol, paired_buy)

    def _execute_buy_order(self, symbol: str, order: Dict):
        """Execute buy order"""
        # Get available cash
        cash_info = self.trading_bot.get_cash_balance()
        available_cash = cash_info['tradeable_cash']

        # Calculate position size
        quantity = self.strategy.calculate_position_size(
            symbol, order['current_price'], available_cash
        )

        if quantity <= 0:
            print(f"  Insufficient cash to buy {symbol}")
            return

        # Queue order in state
        order_details = {
            'quantity': quantity,
            'price': order['current_price'],
            'trigger': '30d_low',
            'order_type': 'market'
        }
        self.state_manager.queue_buy_order(symbol, order_details)

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"EXECUTING BUY ORDER: {symbol}")
            print(f"{'='*70}")
            print(f"Quantity: {quantity}")
            print(f"Price: ${order['current_price']:,.2f}")
            print(f"Total: ${quantity * order['current_price']:,.2f}")
            print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            print(f"{'='*70}\n")

        if not self.dry_run:
            # Execute real order
            result = self.trading_bot.place_cash_buy_order(
                symbol, quantity, order['current_price'], dry_run=False
            )

            if result:
                order_id = result.get('id') if isinstance(result, dict) else None
                if order_id:
                    self.state_manager.update_order_status(
                        symbol, 'buy', 'placed', order_id
                    )
                    print(f"Order placed: {order_id}")

    def _execute_sell_order(self, symbol: str, order: Dict):
        """Execute sell order"""
        quantity = order['quantity']

        # Queue order in state
        order_details = {
            'quantity': quantity,
            'price': order['current_price'],
            'trigger': '30d_high',
            'order_type': 'market'
        }
        self.state_manager.queue_sell_order(symbol, order_details)

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"EXECUTING SELL ORDER: {symbol}")
            print(f"{'='*70}")
            print(f"Quantity: {quantity}")
            print(f"Price: ${order['current_price']:,.2f}")
            print(f"Total: ${quantity * order['current_price']:,.2f}")
            print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            print(f"{'='*70}\n")

        if not self.dry_run:
            # Execute real order
            result = self.trading_bot.place_sell_order(
                symbol, quantity, order['current_price'], dry_run=False
            )

            if result:
                order_id = result.get('id', 'unknown')
                self.state_manager.update_order_status(
                    symbol, 'sell', 'placed', order_id
                )
                print(f"Order placed: {order_id}")

    def _execute_stop_limit_sell_order(self, symbol: str, order: Dict):
        """Execute stop-limit sell order for gap coverage"""
        quantity = order['quantity']
        stop_price = order['stop_price']
        limit_price = order['limit_price']

        order_details = {
            'quantity': quantity,
            'stop_price': stop_price,
            'limit_price': limit_price,
            'price': limit_price,
            'trigger': 'coverage_gap',
            'order_type': 'stop_limit'
        }
        self.state_manager.queue_sell_order(symbol, order_details)

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"EXECUTING STOP-LIMIT SELL: {symbol}")
            print(f"{'='*70}")
            print(f"Quantity: {quantity}")
            print(f"Stop Price: ${stop_price:,.2f}")
            print(f"Limit Price: ${limit_price:,.2f}")
            print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            print(f"{'='*70}\n")

        if not self.dry_run:
            result = self.trading_bot.place_stop_limit_sell_order(
                symbol, quantity, stop_price, limit_price, dry_run=False
            )
            if result:
                order_id = result.get('id') if isinstance(result, dict) else None
                if order_id:
                    self.state_manager.update_order_status(
                        symbol, 'sell', 'placed', order_id
                    )
                    print(f"Order placed: {order_id}")

    def _execute_limit_sell_resubmit(self, symbol: str, order: Dict):
        """Execute limit sell resubmit at original order price"""
        quantity = order['quantity']
        price = order['price']

        order_details = {
            'quantity': quantity,
            'price': price,
            'trigger': 'resubmit',
            'order_type': 'limit'
        }
        self.state_manager.queue_sell_order(symbol, order_details)

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"RESUBMITTING LIMIT SELL: {symbol}")
            print(f"{'='*70}")
            print(f"Quantity: {quantity}")
            print(f"Limit Price: ${price:,.2f} (original order price)")
            print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            print(f"{'='*70}\n")

        if not self.dry_run:
            result = self.trading_bot.place_sell_order(
                symbol, quantity, price, dry_run=False
            )
            if result:
                order_id = result.get('id', 'unknown')
                self.state_manager.update_order_status(
                    symbol, 'sell', 'placed', order_id
                )
                print(f"Order placed: {order_id}")

    def _execute_paired_limit_buy(self, symbol: str, buy_order: Dict):
        """Execute paired limit buy order below the stop-limit sell"""
        quantity = buy_order['quantity']
        price = buy_order['price']
        order_symbol = buy_order['symbol']

        order_details = {
            'quantity': quantity,
            'price': price,
            'trigger': 'paired_dca_buy',
            'order_type': 'limit'
        }
        self.state_manager.queue_buy_order(order_symbol, order_details)

        if self.verbose:
            print(f"\n{'='*70}")
            print(f"PAIRED LIMIT BUY: {order_symbol}")
            print(f"{'='*70}")
            print(f"Quantity: {quantity}")
            print(f"Limit Price: ${price:,.2f}")
            print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
            print(f"{'='*70}\n")

        if not self.dry_run:
            result = self.trading_bot.place_cash_buy_order(
                order_symbol, quantity, price, dry_run=False
            )
            if result:
                order_id = result.get('id') if isinstance(result, dict) else None
                if order_id:
                    self.state_manager.update_order_status(
                        order_symbol, 'buy', 'placed', order_id
                    )
                    print(f"Paired buy placed: {order_id}")

    def print_portfolio_allocation(self):
        """Print current portfolio allocation summary"""
        print(f"\n{'='*70}")
        print("PORTFOLIO ALLOCATION")
        print(f"{'='*70}\n")

        try:
            # Get portfolio summary data (filter display to --ticker symbols)
            symbols_filter = None if self.verbose else self.symbols
            portfolio_data = self.trading_bot.get_portfolio_summary(symbols=symbols_filter)

            if not portfolio_data:
                print("Unable to retrieve portfolio data")
                return

            cash_info = portfolio_data['cash']
            equity = portfolio_data['equity']
            positions = portfolio_data['positions']

            # Calculate allocation percentages
            available_cash = cash_info['tradeable_cash']
            total_position_value = sum(pos['equity'] for pos in positions)

            cash_allocation_pct = (available_cash / equity) * 100 if equity > 0 else 100
            invested_pct = (total_position_value / equity) * 100 if equity > 0 else 0

            print(f"Total Portfolio Value: ${equity:,.2f}\n")
            print(f"Asset Allocation:")
            print(f"  💵 Cash:     {cash_allocation_pct:>6.2f}%  (${available_cash:>12,.2f})")
            print(f"  📈 Invested: {invested_pct:>6.2f}%  (${total_position_value:>12,.2f})")
            print(f"  {'─' * 40}")
            print(f"  📊 Total:    100.00%  (${equity:>12,.2f})\n")

            if positions:
                # Filter to --ticker symbols when not in verbose mode
                if self.verbose:
                    display_positions = positions
                    print(f"Position Breakdown ({len(positions)} holdings):")
                else:
                    display_positions = [p for p in positions if p['symbol'] in self.symbols]
                    print(f"Position Breakdown ({len(display_positions)} holdings; filtered; {len(positions)} total):")
                # Sort positions by equity value descending
                sorted_positions = sorted(display_positions, key=lambda x: x['equity'], reverse=True)

                for pos in sorted_positions:
                    allocation_pct = (pos['equity'] / equity) * 100 if equity > 0 else 0
                    pl_indicator = "📈" if pos['profit_loss'] >= 0 else "📉"

                    print(f"  {pos['symbol']:>6}:   {allocation_pct:>6.2f}%  (${pos['equity']:>12,.2f})  "
                          f"{pl_indicator} {pos['profit_loss_pct']:+.2f}%")
            else:
                print("No positions currently held")

            print(f"\n{'='*70}\n")

        except Exception as e:
            print(f"Error printing portfolio allocation: {e}\n")

    def run_once(self):
        """Run trading system once for all symbols"""
        if self.verbose:
            print(f"\n{'='*70}")
            print("RUNNING TRADING SYSTEM")
            print(f"{'='*70}")
            print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"Symbols: {', '.join(self.symbols)}")
            print(f"{'='*70}\n")

            # Print initial portfolio allocation
            self.print_portfolio_allocation()

        # Fetch open orders once (used by momentum_dca)
        open_orders = []
        if self.strategy_name == 'momentum_dca_long':
            open_orders = self.trading_bot.get_open_orders()

        recent_orders = self.trading_bot.get_recent_orders(days=7)
        recent_option_orders = self.trading_bot.get_recent_option_orders(days=7)

        # Print order book before processing through state manager
        if open_orders:
            # Filter to --ticker symbols when not in verbose mode
            if self.verbose:
                display_orders = open_orders
                filter_note = ""
            else:
                display_orders = [o for o in open_orders if o['symbol'] in self.symbols]
                filter_note = f" (filtered to {', '.join(self.symbols)}; {len(open_orders)} total)"
            print(f"\n📋 Order Book: {len(display_orders)} open order(s){filter_note}")
            buy_orders = [o for o in display_orders if o['side'] == 'BUY']
            sell_orders = [o for o in display_orders if o['side'] == 'SELL']
            if buy_orders:
                print("\n   🟢 BUY ORDERS:")
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
                print("\n   🔴 SELL ORDERS:")
                for order in sell_orders:
                    print(f"\n      {order['symbol']} - {order['order_type']}")
                    print(f"         Quantity: {order['quantity']:.0f} shares")
                    if order['limit_price']:
                        print(f"         Limit Price: ${order['limit_price']:.2f}")
                    if order['stop_price']:
                        print(f"         Stop Price: ${order['stop_price']:.2f}")
                    print(f"         Status: {order['state']}")
                    print(f"         Created: {order['created_at']}")
            print()

        for symbol in self.symbols:
            if self.verbose:
                print(f"\n{'#'*70}")
                print(f"Processing {symbol}")
                print(f"{'#'*70}\n")

            try:
                # 1. Fetch market data
                market_data = self.fetch_market_data(symbol)

                # 2. Calculate metrics
                metrics = self.calculate_metrics(symbol, market_data)
                if self.verbose:
                    print(self.metrics_calculator.format_metrics(symbol, metrics))

                # 3. Execute strategy
                signal = self.execute_strategy(symbol, metrics, open_orders)

                # 4. Process signal
                self.process_signal(symbol, signal, open_orders)

            except Exception as e:
                print(f"Error processing {symbol}: {e}")
                continue

            # Rate limiting between symbols
            time.sleep(1)

        # Log state: local file in dry-run, Netlify Blobs when live
        symbols_filter = None if self.verbose else self.symbols
        portfolio_data = self.trading_bot.get_portfolio_summary(symbols=symbols_filter)

        # Compute drift metrics from cached daily bars (if available)
        drift_metrics = self._compute_drift_metrics()

        log_state_to_blob(
            self.state_manager,
            live=not self.dry_run,
            order_book=open_orders,
            portfolio=portfolio_data,
            drift_metrics=drift_metrics,
            recent_orders=recent_orders,
            recent_option_orders=recent_option_orders,
        )

        # Sync portfolio data to Redis
        sync_to_redis(portfolio_data, recent_orders=recent_orders, recent_option_orders=recent_option_orders, live=not self.dry_run)

        # Refresh dashboard market indicators
        if self.dashboard:
            try:
                extra = {}
                if portfolio_data and isinstance(portfolio_data, dict):
                    extra['options'] = portfolio_data.get('options', [])
                    extra['order_book'] = portfolio_data.get('open_orders', [])
                fetch_and_write_indicators(self.symbols, extra_data=extra)
            except Exception as e:
                print(f"  [indicators] Error refreshing dashboard: {e}")

        if self.verbose:
            # Show summary
            print(f"\n{'='*70}")
            print("RUN COMPLETE")
            print(f"{'='*70}")
            self.state_manager.print_state_summary()

            # Print final portfolio allocation
            print("\nFinal Portfolio Allocation:")
            self.print_portfolio_allocation()

        # Print fill quality stats
        if hasattr(self, 'fill_logger') and self.fill_logger:
            try:
                stats = self.fill_logger.get_stats()
                if stats and stats.get('total_submissions', 0) > 0:
                    print(f"\nFill Quality: {stats.get('total_fills', 0)} fills, "
                          f"avg slippage {stats.get('avg_slippage_bps', 0):.1f} bps, "
                          f"cost impact ${stats.get('total_cost_impact', 0):.2f}")
            except Exception:
                pass

        # Send oncall Slack summary every cycle
        self._send_oncall_summary(open_orders, portfolio_data)

    def _compute_drift_metrics(self) -> dict:
        """Compute rolling drift metrics from cached BTC daily bars.

        Returns a dict with rolling_sharpe, rolling_vol, regime, and
        suggested parameters — or None if data is unavailable.
        """
        try:
            from trading_system.backtests.data_loader import DATA_DIR
            from trading_system.backtests.parameter_optimizer import (
                load_grid_cache,
                suggest_regime_params,
            )
            import json
            import math

            cache_path = DATA_DIR / "BTC_daily.json"
            if not cache_path.exists():
                return None

            with open(cache_path, "r") as f:
                bars = json.load(f)

            if len(bars) < 22:
                return None

            # Use last 22 bars (21 returns) for rolling metrics
            window_bars = bars[-22:]
            closes = [b["close"] for b in window_bars]
            daily_rets = []
            for i in range(1, len(closes)):
                if closes[i - 1] > 0:
                    daily_rets.append((closes[i] - closes[i - 1]) / closes[i - 1])

            if len(daily_rets) < 2:
                return None

            mean_r = sum(daily_rets) / len(daily_rets)
            var = sum((r - mean_r) ** 2 for r in daily_rets) / (len(daily_rets) - 1)
            std = math.sqrt(var) if var > 0 else 0.0
            ann_vol = std * math.sqrt(252) * 100  # percentage

            daily_rf = 0.05 / 252
            rolling_sharpe = ((mean_r - daily_rf) / std * math.sqrt(252)) if std > 0 else 0.0

            # Rolling return
            start_val = closes[0]
            end_val = closes[-1]
            rolling_return = ((end_val - start_val) / start_val * 100) if start_val > 0 else 0.0

            # Regime suggestion (use grid search cache if available)
            grid_cache = load_grid_cache("BTC")
            grid_best = grid_cache.get("best") if grid_cache else None
            regime = suggest_regime_params(ann_vol, grid_search_best=grid_best)

            # Drift alerts
            alerts = []
            if rolling_sharpe < -1.0:
                alerts.append({"metric": "rolling_sharpe", "value": round(rolling_sharpe, 3),
                               "threshold": -1.0})

            result = {
                "as_of": window_bars[-1]["date"],
                "window_days": 21,
                "rolling_sharpe": round(rolling_sharpe, 3),
                "rolling_vol_pct": round(ann_vol, 2),
                "rolling_return_pct": round(rolling_return, 2),
                "regime": regime["regime"],
                "suggested_params": {
                    "stop_offset_pct": regime["stop_offset_pct"],
                    "buy_offset": regime["buy_offset"],
                    "coverage_threshold": regime["coverage_threshold"],
                },
                "regime_rationale": regime["rationale"],
                "source": regime.get("source", "heuristic"),
                "grid_sharpe": regime.get("grid_sharpe"),
                "mean_test_sharpe": grid_cache.get("mean_test_sharpe") if grid_cache else None,
                "degradation": grid_cache.get("degradation") if grid_cache else None,
                "drift_alerts": alerts,
            }
            return result

        except Exception as e:
            print(f"  [drift] Could not compute drift metrics: {e}")
            return None

    def _send_oncall_summary(self, open_orders: List[Dict], portfolio_data: Dict):
        """Send a concise oncall status summary to Slack after each run_once() cycle."""
        try:
            lines = []
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            lines.append(f"Oncall Summary  {now}")
            lines.append("=" * 40)

            # PDT status
            pdt_info = self.trading_bot.get_pdt_status()
            if pdt_info is not None:
                count = pdt_info.get('day_trade_count', 0)
                if pdt_info.get('flagged'):
                    pdt_label = f"PDT: {count}/3 day trades  [ALERT] FLAGGED"
                elif count >= 2:
                    pdt_label = f"PDT: {count}/3 day trades  [WARN]"
                else:
                    pdt_label = f"PDT: {count}/3 day trades  [OK]"
                lines.append(pdt_label)
            else:
                lines.append("PDT: unavailable")

            # Margin / cash info
            if portfolio_data and isinstance(portfolio_data, dict):
                cash_info = portfolio_data.get('cash')
                if cash_info:
                    cash = cash_info.get('tradeable_cash', 0)
                    bp = cash_info.get('buying_power', 0)
                    lines.append("")
                    lines.append("Margin:")
                    lines.append(f"  Cash:         ${cash:,.2f}")
                    lines.append(f"  Buying Power: ${bp:,.2f}")

            # Order book
            if open_orders:
                if self.verbose:
                    display_orders = open_orders
                else:
                    display_orders = [o for o in open_orders if o['symbol'] in self.symbols]
                symbols_label = ', '.join(self.symbols) if not self.verbose else 'all'
                lines.append("")
                lines.append(f"Order Book ({symbols_label}): {len(display_orders)} order(s)")
                for o in display_orders:
                    side = o.get('side', '?')
                    sym = o.get('symbol', '?')
                    qty = o.get('quantity', 0)
                    otype = o.get('order_type', '?')
                    lmt = f" lmt ${o['limit_price']:.2f}" if o.get('limit_price') else ""
                    stp = f"  stp ${o['stop_price']:.2f}" if o.get('stop_price') else ""
                    lines.append(f"  {side:<4} {sym:<6} x{qty:.0f}  {otype}{lmt}{stp}")
            else:
                lines.append("")
                lines.append("Order Book: 0 order(s)")

            # Positions
            if portfolio_data and isinstance(portfolio_data, dict):
                positions = portfolio_data.get('positions', [])
                if self.verbose:
                    display_positions = positions
                else:
                    display_positions = [p for p in positions if p['symbol'] in self.symbols]
                symbols_label = ', '.join(self.symbols) if not self.verbose else 'all'
                lines.append("")
                lines.append(f"Positions ({symbols_label}): {len(display_positions)}")
                for pos in display_positions:
                    pl_sign = "(+)" if pos.get('profit_loss', 0) >= 0 else "(-)"
                    lines.append(
                        f"  {pos['symbol']:<6} x{pos['quantity']:<7}  "
                        f"${pos['equity']:>10,.2f}  {pl_sign} {pos.get('profit_loss_pct', 0):+.2f}%"
                    )

            # Fill quality summary
            if hasattr(self, 'fill_logger') and self.fill_logger:
                try:
                    stats = self.fill_logger.get_stats()
                    if stats and stats.get('total_submissions', 0) > 0:
                        lines.append("")
                        lines.append("Fill Quality:")
                        lines.append(f"  Fills today: {stats.get('total_fills', 0)}")
                        lines.append(f"  Avg slippage: {stats.get('avg_slippage_bps', 0):.1f} bps")
                        lines.append(f"  Total cost: ${stats.get('total_cost_impact', 0):.2f}")
                except Exception:
                    pass

            message = "```\n" + "\n".join(lines) + "\n```"
            send_slack_alert(message, emoji=":chart_with_upwards_trend:")

        except Exception as e:
            print(f"  [oncall] Error sending Slack summary: {e}")

    def run_backtest(self):
        """Run parameter grid search on cached daily data for each symbol.

        Loads cached daily bars, runs grid search, saves results to JSON cache,
        and prints a summary of best params.
        """
        import json
        from trading_system.backtests.data_loader import DATA_DIR
        from trading_system.backtests.parameter_optimizer import (
            run_regime_grid_search,
            save_grid_cache,
        )

        print(f"\n{'='*70}")
        print("PARAMETER GRID SEARCH (BACKTEST)")
        print(f"{'='*70}\n")

        results = {}
        for symbol in self.symbols:
            cache_path = DATA_DIR / f"{symbol}_daily.json"
            if not cache_path.exists():
                print(f"  [{symbol}] No cached daily data at {cache_path} — skipping")
                continue

            with open(cache_path, "r") as f:
                bars = json.load(f)

            if self.recent_days and len(bars) > self.recent_days:
                bars = bars[-self.recent_days:]
                print(f"  [{symbol}] Trimmed to last {len(bars)} bars (--recent {self.recent_days})")

            if len(bars) < 30:
                print(f"  [{symbol}] Only {len(bars)} bars — need at least 30, skipping")
                continue

            print(f"  [{symbol}] Running grid search on {len(bars)} daily bars...")
            grid_result = run_regime_grid_search(
                bars=bars,
                symbol=symbol,
                shares=1,
                price=bars[-1]["close"],
            )
            cache_file = save_grid_cache(grid_result, symbol)
            results[symbol] = grid_result

            best = grid_result["best"]
            if best:
                print(f"  [{symbol}] Best params (last fold):")
                print(f"    stop_offset_pct:    {best.get('stop_offset_pct', 'N/A')}")
                print(f"    buy_offset:         {best.get('buy_offset', 'N/A')}")
                print(f"    coverage_threshold: {best.get('coverage_threshold', 'N/A')}")
            print(f"    Regime:             {grid_result['regime']}")

            # Walk-forward CV summary
            cv_folds = grid_result.get("cv_folds", [])
            n_folds = grid_result.get("n_folds", 0)
            print(f"    --- Walk-forward CV ({n_folds} folds) ---")
            for fold in cv_folds:
                print(f"    Fold {fold['fold']}: train {fold['train_bars']}, test {fold['test_bars']}"
                      f" | train Sharpe {fold['train_sharpe']:.3f}, test Sharpe {fold['test_sharpe']:.3f}")

            mean_test = grid_result.get("mean_test_sharpe")
            std_test = grid_result.get("std_test_sharpe")
            if mean_test is not None and std_test is not None:
                print(f"    Mean test Sharpe:   {mean_test:.3f} +/- {std_test:.3f}")

            degradation = grid_result.get("degradation")
            degradation_str = f"{degradation:.2f}x" if degradation is not None else "N/A (train Sharpe <= 0)"
            print(f"    Degradation:        {degradation_str}")
            if degradation is not None and degradation < 0.5:
                print(f"    WARNING: Significant Sharpe degradation -- params may be overfit")
            print(f"    Cached to:          {cache_file}\n")

        if not results:
            print("  No symbols had cached data. Run the system first to populate daily caches.")

        print(f"{'='*70}\n")
        return results

    def run_continuous(self, interval_minutes: int = 5):
        """
        Run trading system continuously

        Args:
            interval_minutes: Minutes between runs
        """
        import datetime as _dt

        print(f"\nStarting continuous mode (every {interval_minutes} minutes)")
        print("Press Ctrl+C to stop\n")

        self._last_backtest_date = None

        try:
            while True:
                # Weekly Sunday auto-backtest
                today = _dt.date.today()
                if today.weekday() == 6 and self._last_backtest_date != today:
                    print("\n  [backtest] Weekly Sunday grid search...")
                    try:
                        self.run_backtest()
                    except Exception as e:
                        print(f"  [backtest] Error during weekly grid search: {e}")
                    self._last_backtest_date = today

                self.run_once()

                print(f"\nWaiting {interval_minutes} minutes until next run...")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n\nStopped by user")


def _sync_slack_webhook_from_netlify():
    """Fetch SLACK_WEBHOOK_URL from Netlify site env vars and persist to .env."""
    token = os.getenv("NETLIFY_API_TOKEN")
    site_id = os.getenv("NETLIFY_SITE_ID")
    if not token or not site_id:
        return

    try:
        resp = requests.get(
            f"https://api.netlify.com/api/v1/sites/{site_id}/env",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        # /env returns a list of {key, values} objects
        webhook_url = None
        for var in resp.json():
            if var.get("key") == "SLACK_WEBHOOK_URL":
                values = var.get("values", [])
                if values:
                    webhook_url = values[0].get("value")
                break
        if not webhook_url:
            return

        os.environ["SLACK_WEBHOOK_URL"] = webhook_url

        # Append to .env so future runs don't need the API call
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            contents = env_path.read_text()
            if "SLACK_WEBHOOK_URL" not in contents:
                with open(env_path, "a") as f:
                    f.write(f"\nSLACK_WEBHOOK_URL={webhook_url}\n")
                print(f"Synced SLACK_WEBHOOK_URL from Netlify → .env")
    except Exception as e:
        print(f"Could not fetch SLACK_WEBHOOK_URL from Netlify: {e}")


def main():
    """Main entry point"""
    import argparse
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    # Pull SLACK_WEBHOOK_URL from Netlify env if not set locally
    if not os.getenv("SLACK_WEBHOOK_URL"):
        _sync_slack_webhook_from_netlify()

    # Parse arguments
    parser = argparse.ArgumentParser(description='Trading System')
    parser.add_argument(
        '--live',
        action='store_true',
        help='Execute LIVE trades (default is dry run)'
    )
    parser.add_argument(
        '--continuous',
        action='store_true',
        help='Run continuously (default is single run)'
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=5,
        help='Minutes between runs in continuous mode (default: 5)'
    )

    parser.add_argument(
        '--strategy',
        type=str,
        choices=['momentum_dca_long', 'breakout'],
        default='momentum_dca_long',
        help='Trading strategy to use (default: momentum_dca_long)'
    )
    parser.add_argument(
        '--ticker',
        type=str,
        nargs='+',
        default=None,
        help='Symbols to trade (default: BTC SPY QQQ AMZN). Example: --ticker BTC SPY'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Show detailed output (metrics, portfolio allocation, etc.)'
    )
    parser.add_argument(
        '--dashboard',
        action='store_true',
        help='Fetch market indicators and write dashboard/market_data.json each cycle'
    )
    parser.add_argument(
        '--backtest',
        action='store_true',
        help='Run parameter grid search on cached daily data and update regime suggestions'
    )
    parser.add_argument(
        '--recent', type=int, default=None, metavar='DAYS',
        help='Limit backtest to the last N daily bars (e.g. --recent 90)'
    )

    args = parser.parse_args()

    # Get API key
    api_key = os.getenv('TWELVE_DATA_API_KEY', 'f2c57fbb0a794024b0defff74af45686')

    # Define symbols
    symbols = [s.upper() for s in args.ticker] if args.ticker else ['BTC', 'SPY', 'QQQ', 'AMZN']

    # Show warning for live trading
    if args.live:
        print("\n" + "="*70)
        print("WARNING: LIVE TRADING MODE ENABLED")
        print("="*70)
        print("Real orders will be placed on your Robinhood account!")
        print("Press Ctrl+C within 10 seconds to cancel...")
        print("="*70 + "\n")
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            print("\nCancelled by user\n")
            return

    # Initialize system
    system = TradingSystem(
        twelve_data_api_key=api_key,
        symbols=symbols,
        position_size_pct=0.25,
        dry_run=not args.live,
        strategy_name=args.strategy,
        verbose=args.verbose,
        dashboard=args.dashboard,
        recent_days=args.recent,
    )

    # Run backtest if requested (standalone mode — exit after)
    if args.backtest:
        system.run_backtest()
        return

    # Run system
    if args.continuous:
        system.run_continuous(interval_minutes=args.interval)
    else:
        system.run_once()

    if args.verbose:
        # Show usage
        print("\n" + "="*70)
        print("USAGE")
        print("="*70)
        if args.live:
            print("LIVE mode - Real orders were placed")
            print("\nTo run in DRY RUN mode:")
            print("  python -m trading_system.main")
        else:
            print("DRY RUN mode - No real orders were placed")
            print("\nTo execute LIVE trades:")
            print("  python -m trading_system.main --live")

        print("\nOther options:")
        print("  --continuous           # Run continuously")
        print("  --interval 10          # Check every 10 minutes")
        print("  --help                 # Show all options")
        print("="*70 + "\n")


if __name__ == "__main__":
    main()
