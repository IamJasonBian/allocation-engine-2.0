import os
from dotenv import load_dotenv

from app.app_config import load_app_config

load_dotenv()
load_dotenv(".env.local", override=True)

# Mode-based file config (configs/config.json) — also loads the mode's env file.
_app_config = load_app_config()


class Config:
    # -- App mode (configs/config.json; APP_MODE env overrides) --
    APP_MODE = _app_config.app_mode
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

    # -- IBKR --
    # The gateway socket is unauthenticated, so IBKR_HOST must only ever be
    # localhost or an SSH-tunnel endpoint — never a public address. See
    # docs/IBKR_GATEWAY.md for where the gateway process runs.
    IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
    IBKR_PORT = int(os.getenv("IBKR_PORT", "4002"))  # gateway paper; 4001 live
    IBKR_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "1"))
    IBKR_PAPER = os.getenv("IBKR_PAPER", "true").lower() == "true"

    # -- Robinhood --
    # Credentials, TOTP and device identity live in the auth-service box only;
    # this service never logs in. Email is kept for status reporting and the
    # account number for selecting the automated account.
    RH_MAIN_EMAIL = os.getenv("RH_MAIN_EMAIL", "")
    RH_AUTOMATED_ACCOUNT_NUMBER = os.getenv("RH_AUTOMATED_ACCOUNT_NUMBER", "")
    RH_RETRY_HOUR_ET = int(os.getenv("RH_RETRY_HOUR_ET", "11"))

    # -- Auth-service (Robinhood session on the external box) --
    # Base URL must be https — the request token is sent as a Bearer header.
    AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "")
    RH_AUTH_SERVICE_REQUEST_TOKEN = os.getenv("RH_AUTH_SERVICE_REQUEST_TOKEN", "")
    AUTH_SERVICE_TIMEOUT = int(os.getenv("AUTH_SERVICE_TIMEOUT", "30"))
    # Bearer token gating the order-mutation routes (replace/cancel/lookup),
    # same scheme as the render-logs netlify function. Fail closed: unset means
    # those routes refuse to run.
    DASHBOARD_REQUEST_TOKEN = os.getenv("DASHBOARD_REQUEST_TOKEN", "")

    # Callers of /api/robinhood/* must present this token. Those routes relay
    # to the box using OUR bearer, so without a gate anyone on the internet can
    # place orders through them. Unset means the routes are closed, not open.
    RH_PROXY_TOKEN = os.getenv("RH_PROXY_TOKEN", "")

    # -- Trailing-stop sweeper (runs in the background engine loop) --
    # Universe beyond current positions; comma-separated symbols.
    STOP_TICKERS = os.getenv("STOP_TICKERS", "")
    # Sweeper writes stay dry-run unless explicitly armed.
    STOP_SWEEP_DRY_RUN = os.getenv("STOP_SWEEP_DRY_RUN", "true").lower() == "true"
    # Earliest ET hour for the daily sweep (0 = first tick of the day).
    STOP_SWEEP_HOUR_ET = int(os.getenv("STOP_SWEEP_HOUR_ET", "0"))
    # Vol-scaled trail percentages (docs/TRAILING_STOP_WATERFALL.md).
    # Off = flat STOP_TRAIL_PERCENT, byte-for-byte today's behavior.
    STOP_VOL_SCALED = os.getenv("STOP_VOL_SCALED", "false").lower() == "true"
    STOP_DB_PATH = os.getenv(
        "STOP_DB_PATH",
        os.path.join(os.path.dirname(__file__), "..", "data", "stops.sqlite3"),
    )

    # -- Trading DB write path (5thstreetcapital Netlify functions) --
    TRADING_DB_URL = os.getenv(
        "TRADING_DB_URL", "https://5thstreetcapital.org/.netlify/functions"
    )
    TRADING_DB_TOKEN = os.getenv("TRADING_DB_TOKEN", "")
    TRADING_DB_SYNC_SECONDS = int(os.getenv("TRADING_DB_SYNC_SECONDS", "900"))

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

    # -- Storage routing (from configs/config.json, per app mode) --
    # broker = live Robinhood reads; local = {LOCAL_STORAGE_DIR}/trading.db (SQLite)
    # LOCAL_STORAGE_DIR is None in broker modes that omit `storage_dir`.
    STORAGE_BACKEND = _app_config.mode.storage_backend
    LOCAL_STORAGE_DIR = _app_config.mode.storage_dir

    # -- S3 (order event storage) --
    S3_BUCKET = os.getenv("S3_BUCKET", "")
    S3_PREFIX = os.getenv("S3_PREFIX", "order-events")
    AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
    # AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY read by boto3 automatically
