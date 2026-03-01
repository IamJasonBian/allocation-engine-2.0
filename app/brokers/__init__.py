"""Broker registry — lazy-creates and caches broker clients by name."""

from app.brokers.base import BrokerClient

_broker_cache: dict[str, BrokerClient] = {}


def get_broker(name: str) -> BrokerClient:
    """Get or create a broker client by name ('alpaca' or 'robinhood')."""
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
        client = RobinhoodTrader(
            email=config["RH_USER"],
            password=config["RH_PASS"],
            totp_secret=config.get("RH_TOTP_SECRET", ""),
            device_token=config.get("RH_DEVICE_TOKEN", ""),
            pickle_name=config.get("RH_PICKLE_NAME", "taipei_session"),
        )
    else:
        raise ValueError(f"Unknown broker: {name}")

    _broker_cache[name] = client
    return client
