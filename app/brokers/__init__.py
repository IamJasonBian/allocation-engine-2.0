"""Broker registry — lazy-creates and caches broker clients by name."""

from app.brokers.base import BrokerClient

_broker_cache: dict[str, BrokerClient] = {}


def get_broker(name: str) -> BrokerClient:
    """Get or create a broker client by name ('alpaca', 'robinhood' or 'ibkr')."""
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
        # No credentials here — the auth-service box owns the Robinhood
        # session and vends the access token (see app.box_session).
        client = RobinhoodTrader(
            email=config.get("RH_MAIN_EMAIL", ""),
            account_number=config.get("RH_AUTOMATED_ACCOUNT_NUMBER", ""),
        )
    elif name == "ibkr":
        from app.brokers.ibkr_client import IBKRTrader
        # No credentials here either — the gateway box owns the IBKR session;
        # this client only dials its socket (see docs/IBKR_GATEWAY.md).
        client = IBKRTrader(
            host=config["IBKR_HOST"],
            port=config["IBKR_PORT"],
            client_id=config["IBKR_CLIENT_ID"],
            paper=config["IBKR_PAPER"],
        )
    else:
        raise ValueError(f"Unknown broker: {name}")

    _broker_cache[name] = client
    return client


def clear_broker(name: str):
    """Remove a broker from the cache, forcing re-creation on next get_broker() call."""
    _broker_cache.pop(name, None)
