import os
from dotenv import load_dotenv

load_dotenv()

ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"

RUNTIME_SERVICE_URL = os.getenv(
    "RUNTIME_SERVICE_URL",
    "https://route-runtime-service.netlify.app/api",
)

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
