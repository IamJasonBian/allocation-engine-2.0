from abc import ABC, abstractmethod
from typing import Dict, Optional

from trading_system.entities.Ticker import Ticker


class BaseStrategy(ABC):

    @abstractmethod
    def analyze_symbol(self, symbol: str, metrics: Dict,
                       current_position: Optional[Dict],
                       ticker: Ticker) -> Dict:
        """Analyze a symbol and return a signal dict."""

    @abstractmethod
    def calculate_position_size(self, symbol: str, price: float,
                                available_cash: float) -> int:
        """Return the number of shares/units to trade."""

    @abstractmethod
    def format_signal(self, symbol: str, signal_data: Dict) -> str:
        """Return a formatted string for display."""
