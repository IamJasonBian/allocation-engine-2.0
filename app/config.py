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
    # Static device token — approved by Robinhood for this account.
    # Stored in Netlify env vars and Render env vars.
    # Only change this if Robinhood revokes the device server-side.
    RH_DEVICE_TOKEN = os.getenv(
        "RH_DEVICE_TOKEN", "8508c7fc-a1f3-bc44-b23e-0f28b6d0ecdb"
    )
    RH_PICKLE_NAME = os.getenv("RH_PICKLE_NAME", "taipei_session")

    # -- Robinhood session persistence --
    RH_RETRY_HOUR_ET = int(os.getenv("RH_RETRY_HOUR_ET", "11"))

    @classmethod
    def rh_credentials(cls) -> tuple[str, str]:
        """Return (email, password) for the active Robinhood account."""
        if cls.RH_ACTIVE_ACCOUNT == "automated":
            return cls.RH_AUTO_EMAIL, cls.RH_AUTO_PASSWORD
        return cls.RH_MAIN_EMAIL, cls.RH_MAIN_PASSWORD

    # -- Interactive Brokers (IB Gateway via ib_async / TWS socket) --
    IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
    IBKR_PORT = int(os.getenv("IBKR_PORT", "4002"))          # 4002 paper / 4001 live
    IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))
    IBKR_ACCOUNT_ID = os.getenv("IBKR_ACCOUNT_ID", "")
    IBKR_PAPER = os.getenv("IBKR_PAPER", "true").lower() == "true"
    IBKR_PEG_DELTA_DEFAULT = float(os.getenv("IBKR_PEG_DELTA_DEFAULT", "0.5"))
    IBKR_MAX_OPTION_ORDER_QTY = int(os.getenv("IBKR_MAX_OPTION_ORDER_QTY", os.getenv("MAX_ORDER_QTY", "50")))
    IBKR_OPEN_BUFFER_MIN = int(os.getenv("IBKR_OPEN_BUFFER_MIN", "2"))
    IBKR_CLOSE_BUFFER_MIN = int(os.getenv("IBKR_CLOSE_BUFFER_MIN", "5"))

    # -- Runtime service --
    RUNTIME_SERVICE_URL = os.getenv(
        "RUNTIME_SERVICE_URL",
        "https://route-runtime-service.netlify.app/api",
    )

    # -- Engine --
    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
    DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
    ENGINE_ENABLED = os.getenv("ENGINE_ENABLED", "true").lower() == "true"
    ENGINE_BROKER = os.getenv("ENGINE_BROKER", "robinhood")
    DATA_BROKER = os.getenv("DATA_BROKER", "alpaca")
    MAX_ORDER_QTY = int(os.getenv("MAX_ORDER_QTY", "50"))

    # -- S3 (order event storage) --
    S3_BUCKET = os.getenv("S3_BUCKET", "")
    S3_PREFIX = os.getenv("S3_PREFIX", "order-events")
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    # AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY read by boto3 automatically
