import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # -- Flask --
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    DEBUG = os.getenv("FLASK_DEBUG", "false").lower() == "true"

    # -- Broker selection --
    ENABLED_BROKERS = [
        b.strip() for b in os.getenv("ENABLED_BROKERS", "robinhood").split(",")
    ]
    DEFAULT_BROKER = os.getenv("DEFAULT_BROKER", "robinhood")

    # -- Alpaca --
    ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
    ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
    ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

    # -- Robinhood --
    RH_USER = os.getenv("RH_USER", "")
    RH_PASS = os.getenv("RH_PASS", "")
    RH_TOTP_SECRET = os.getenv("RH_TOTP_SECRET", "")
    RH_PICKLE_NAME = os.getenv("RH_PICKLE_NAME", "taipei_session")

    # -- Robinhood session persistence --
    RH_RETRY_HOUR_ET = int(os.getenv("RH_RETRY_HOUR_ET", "11"))

    # -- Runtime service --
    RUNTIME_SERVICE_URL = os.getenv(
        "RUNTIME_SERVICE_URL",
        "https://route-runtime-service.netlify.app/api",
    )

    # -- Engine --
    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    ENGINE_ENABLED = os.getenv("ENGINE_ENABLED", "true").lower() == "true"
    ENGINE_BROKER = os.getenv("ENGINE_BROKER", "alpaca")
