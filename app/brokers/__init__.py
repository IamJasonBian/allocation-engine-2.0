"""Broker registry — lazy-creates and caches broker clients by name."""

from app.brokers.base import BrokerClient

_broker_cache: dict[str, BrokerClient] = {}


def get_broker(name: str) -> BrokerClient:
    """Get or create a broker client by name ('alpaca', 'robinhood', or 'ibkr')."""
    if name in _broker_cache:
        return _broker_cache[name]

    from flask import current_app
    config = current_app.config

    if name == "alpaca":
        from app.brokers.alpaca_client import AlpacaTrader
        client = AlpacaTrader(
            api_key=config["ALPACA_API_KEY"],
            secret_key=config["ALPACA_SECRET_KEY"],
            paper=config["ALPACA_PAPER"],
        )
    elif name == "robinhood":
        from app.brokers.robinhood_client import RobinhoodTrader
        from app.config import Config
        email, password = Config.rh_credentials()
        client = RobinhoodTrader(
            email=email,
            password=password,
            totp_secret=config.get("RH_TOTP_SECRET", ""),
            pickle_name=config.get("RH_PICKLE_NAME", "taipei_session"),
            account_number=config.get("RH_AUTOMATED_ACCOUNT_NUMBER", ""),
        )
    elif name == "ibkr":
        from app.brokers.ibkr import IBKRTrader
        client = IBKRTrader(
            account_id=config.get("IBKR_ACCOUNT_ID", ""),
            host=config.get("IBKR_HOST", "127.0.0.1"),
            port=config.get("IBKR_PORT", 4002),
            client_id=config.get("IBKR_CLIENT_ID", 1),
            paper=config.get("IBKR_PAPER", True),
            peg_delta_default=config.get("IBKR_PEG_DELTA_DEFAULT", 0.5),
            max_option_order_qty=config.get("IBKR_MAX_OPTION_ORDER_QTY", 50),
        )
    else:
        raise ValueError(f"Unknown broker: {name}")

    _broker_cache[name] = client
    return client


def clear_broker(name: str):
    """Remove a broker from the cache, forcing re-creation on next get_broker() call."""
    _broker_cache.pop(name, None)
