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
    # Account selection: "main" or "automated"
    RH_ACTIVE_ACCOUNT = os.getenv("RH_ACTIVE_ACCOUNT", "main")

    # Main account credentials
    RH_MAIN_EMAIL = os.getenv("RH_MAIN_EMAIL", "")
    RH_MAIN_PASSWORD = os.getenv("RH_MAIN_PASSWORD", "")

    # Automated trading account credentials
    RH_AUTO_EMAIL = os.getenv("RH_AUTO_EMAIL", "")
    RH_AUTO_PASSWORD = os.getenv("RH_AUTO_PASSWORD", "")

    RH_AUTOMATED_ACCOUNT_NUMBER = os.getenv("RH_AUTOMATED_ACCOUNT_NUMBER", "")
    RH_TOTP_SECRET = os.getenv("RH_TOTP_SECRET", "")
    RH_DEVICE_TOKEN = os.getenv("RH_DEVICE_TOKEN", "")
    RH_PICKLE_NAME = os.getenv("RH_PICKLE_NAME", "taipei_session")

    @classmethod
    def rh_credentials(cls) -> tuple[str, str]:
        """Return (email, password) for the active Robinhood account."""
        if cls.RH_ACTIVE_ACCOUNT == "automated":
            return cls.RH_AUTO_EMAIL, cls.RH_AUTO_PASSWORD
        return cls.RH_MAIN_EMAIL, cls.RH_MAIN_PASSWORD

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
