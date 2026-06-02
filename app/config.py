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

    # -- Interactive Brokers (Client Portal Web API, OAuth 1.0a) --
    IBKR_ACCOUNT_ID = os.getenv("IBKR_ACCOUNT_ID", "")
    IBKR_PAPER = os.getenv("IBKR_PAPER", "true").lower() == "true"
    IBKR_CONSUMER_KEY = os.getenv("IBKR_CONSUMER_KEY", "")
    IBKR_ACCESS_TOKEN = os.getenv("IBKR_ACCESS_TOKEN", "")
    IBKR_ACCESS_TOKEN_SECRET = os.getenv("IBKR_ACCESS_TOKEN_SECRET", "")
    IBKR_DH_PRIME = os.getenv("IBKR_DH_PRIME", "")
    IBKR_SIGNATURE_KEY = os.getenv("IBKR_SIGNATURE_KEY", "")   # RSA private key PEM contents
    IBKR_ENCRYPTION_KEY = os.getenv("IBKR_ENCRYPTION_KEY", "") # RSA private key PEM contents
    IBKR_MAX_OPTION_ORDER_QTY = int(os.getenv("IBKR_MAX_OPTION_ORDER_QTY", os.getenv("MAX_ORDER_QTY", "50")))

    @classmethod
    def ibkr_key_files(cls) -> tuple[str, str]:
        """Write IBKR signature/encryption PEM env vars to temp files; return (signature_path, encryption_path)."""
        import tempfile, os as _os
        def _write(content: str, suffix: str) -> str:
            if not content:
                return ""
            fd, path = tempfile.mkstemp(suffix=suffix)
            with _os.fdopen(fd, "w") as f:
                f.write(content)
            return path
        return _write(cls.IBKR_SIGNATURE_KEY, "_sig.pem"), _write(cls.IBKR_ENCRYPTION_KEY, "_enc.pem")

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
