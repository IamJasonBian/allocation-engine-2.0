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

    # -- Auth-service (Robinhood session on the external box) --
    # Base URL must be https — the request token is sent as a Bearer header.
    AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "")
    RH_AUTH_SERVICE_REQUEST_TOKEN = os.getenv("RH_AUTH_SERVICE_REQUEST_TOKEN", "")
    AUTH_SERVICE_TIMEOUT = int(os.getenv("AUTH_SERVICE_TIMEOUT", "30"))

    # -- Trailing-stop sweeper (runs in the background engine loop) --
    # Universe beyond current positions; comma-separated symbols.
    STOP_TICKERS = os.getenv("STOP_TICKERS", "")
    # Sweeper writes stay dry-run unless explicitly armed.
    STOP_SWEEP_DRY_RUN = os.getenv("STOP_SWEEP_DRY_RUN", "true").lower() == "true"
    # Earliest ET hour for the daily sweep (0 = first tick of the day).
    STOP_SWEEP_HOUR_ET = int(os.getenv("STOP_SWEEP_HOUR_ET", "0"))
    STOP_DB_PATH = os.getenv(
        "STOP_DB_PATH",
        os.path.join(os.path.dirname(__file__), "..", "data", "stops.sqlite3"),
    )

    # -- Claude Code reauth (in-box login flow) --
    # Command that starts the Claude login and prints a browser callback URL.
    CLAUDE_LOGIN_CMD = os.getenv("CLAUDE_LOGIN_CMD", "claude setup-token")
    # File that appears/updates once Claude verification completes.
    CLAUDE_CREDENTIALS_PATH = os.getenv(
        "CLAUDE_CREDENTIALS_PATH",
        os.path.expanduser("~/.claude/.credentials.json"),
    )

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
