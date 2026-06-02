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
        from app.config import Config
        sig_path, enc_path = Config.ibkr_key_files()
        client = IBKRTrader(
            account_id=config.get("IBKR_ACCOUNT_ID", ""),
            paper=config.get("IBKR_PAPER", True),
            consumer_key=config.get("IBKR_CONSUMER_KEY", ""),
            access_token=config.get("IBKR_ACCESS_TOKEN", ""),
            access_token_secret=config.get("IBKR_ACCESS_TOKEN_SECRET", ""),
            dh_prime=config.get("IBKR_DH_PRIME", ""),
            signature_key_path=sig_path,
            encryption_key_path=enc_path,
        )
    else:
        raise ValueError(f"Unknown broker: {name}")

    _broker_cache[name] = client
    return client


def clear_broker(name: str):
    """Remove a broker from the cache, forcing re-creation on next get_broker() call."""
    _broker_cache.pop(name, None)
