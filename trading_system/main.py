"""
Main Trading System Orchestrator
Coordinates market data, strategy execution, and order management
"""

import os
import sys
from datetime import datetime
from typing import List, Dict
import time

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_system.data_providers.twelve_data import TwelveDataProvider  # noqa: E402
from trading_system.utils.metrics import MetricsCalculator  # noqa: E402
from trading_system.strategies.breakout_strategy import BreakoutStrategy  # noqa: E402
from trading_system.strategies.momentum_dca_strategy import MomentumDcaStrategy  # noqa: E402
from trading_system.state.state_manager import StateManager  # noqa: E402
from utils.safe_cash_bot import SafeCashBot  # noqa: E402


class TradingSystem:
    """Main trading system orchestrator"""

    def __init__(self, twelve_data_api_key: str, symbols: List[str],
                 position_size_pct: float = 0.25, dry_run: bool = True,
                 strategy_name: str = 'momentum_dca',
                 verbose: bool = False):
        """
        Initialize trading system

        Args:
            twelve_data_api_key: Twelve Data API key
            symbols: List of symbols to trade
            position_size_pct: Position size as percentage of portfolio
            dry_run: If True, simulates orders without execution
            strategy_name: Strategy to use ('momentum_dca' or 'breakout')
            verbose: If True, show detailed output (metrics, portfolio, etc.)
        """
        self.symbols = symbols
        self.dry_run = dry_run
        self.strategy_name = strategy_name
        self.verbose = verbose

        # Initialize components
        self.data_provider = TwelveDataProvider(twelve_data_api_key)
        self.metrics_calculator = MetricsCalculator()
        self.state_manager = StateManager()
        self.trading_bot = SafeCashBot()

        if strategy_name == 'momentum_dca':
            self.strategy = MomentumDcaStrategy(symbols)
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

        if self.strategy_name == 'momentum_dca':
            # Load broker sell orders into the symbol's Ticker
            self.state_manager.load_broker_sell_orders(symbol, open_orders or [])
            ticker = self.state_manager.get_ticker(symbol)
            signal = self.strategy.analyze_symbol(symbol, metrics, current_position, ticker)
        else:
            signal = self.strategy.analyze_symbol(symbol, metrics, current_position)

        return signal

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

        # Skip execution if 2 or more orders already on the book (max 1 buy + 1 sell)
        if open_orders and len(open_orders) >= 2:
            print(f"⚠️  Skipping order execution: {len(open_orders)} orders on book (max 2)")
            return

        order = signal['order']

        if order['action'] == 'buy':
            self._execute_buy_order(symbol, order)
        elif order['action'] == 'sell':
            self._execute_sell_order(symbol, order)
        elif order['action'] == 'stop_limit_sell':
            has_paired_buy = signal.get('paired_buy') is not None

            if has_paired_buy:
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
                    self._execute_paired_limit_buy(symbol, signal['paired_buy'])
                else:
                    self._execute_paired_limit_buy(symbol, signal['paired_buy'])
                    self._execute_stop_limit_sell_order(symbol, order)
            else:
                self._execute_stop_limit_sell_order(symbol, order)
        elif order['action'] == 'limit_sell':
            self._execute_limit_sell_resubmit(symbol, order)

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
            # Get portfolio summary data
            portfolio_data = self.trading_bot.get_portfolio_summary()

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
                print(f"Position Breakdown ({len(positions)} holdings):")
                # Sort positions by equity value descending
                sorted_positions = sorted(positions, key=lambda x: x['equity'], reverse=True)

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
        if self.strategy_name == 'momentum_dca':
            open_orders = self.trading_bot.get_open_orders()

        # Always print order book in live mode
        if not self.dry_run and open_orders:
            print(f"\n📋 Order Book: {len(open_orders)} open order(s)")
            buy_orders = [o for o in open_orders if o['side'] == 'BUY']
            sell_orders = [o for o in open_orders if o['side'] == 'SELL']
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

        if self.verbose:
            # Show summary
            print(f"\n{'='*70}")
            print("RUN COMPLETE")
            print(f"{'='*70}")
            self.state_manager.print_state_summary()

            # Print final portfolio allocation
            print("\nFinal Portfolio Allocation:")
            self.print_portfolio_allocation()

    def run_continuous(self, interval_minutes: int = 5):
        """
        Run trading system continuously

        Args:
            interval_minutes: Minutes between runs
        """
        print(f"\nStarting continuous mode (every {interval_minutes} minutes)")
        print("Press Ctrl+C to stop\n")

        try:
            while True:
                self.run_once()

                print(f"\nWaiting {interval_minutes} minutes until next run...")
                time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n\nStopped by user")


def main():
    """Main entry point"""
    import argparse
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

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
        choices=['momentum_dca', 'breakout'],
        default='momentum_dca',
        help='Trading strategy to use (default: momentum_dca)'
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
        verbose=args.verbose
    )

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
