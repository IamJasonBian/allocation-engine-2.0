"""Configuration for the auth-service.

Loaded from an INI file — `env.prod` by default (override with AUTH_SERVICE_ENV).
Credentials themselves live in GCP Secret Manager; this file holds only the
project id and the *names* of the secrets. Any value may be overridden by an
environment variable of the same name as the module attribute below.

Uses stdlib configparser (no python-dotenv) to keep the footprint tiny.
"""

import configparser
import os
from pathlib import Path

_ENV_FILE = os.getenv("AUTH_SERVICE_ENV", "env.prod")

# interpolation=None so '%' in tokens/passwords isn't treated specially.
_parser = configparser.ConfigParser(interpolation=None)
_candidate = Path(_ENV_FILE)
if not _candidate.is_absolute():
    _candidate = Path(__file__).resolve().parent / _ENV_FILE
if _candidate.exists():
    _parser.read(_candidate)


def _get(section: str, key: str, default: str = "") -> str:
    return _parser.get(section, key, fallback=default)


# --- GCP ---
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", _get("gcp", "project_id"))

# --- [rh.auth] Robinhood login — values are GCP Secret Manager secret names ---
SECRET_USERNAME = os.getenv("SECRET_USERNAME", _get("rh.auth", "user"))
SECRET_PASSWORD = os.getenv("SECRET_PASSWORD", _get("rh.auth", "pass"))
SECRET_TOTP = os.getenv("SECRET_TOTP", _get("rh.auth", "totp"))

# Stable device identity. Pinning the approved device token keeps Robinhood from
# treating us as a new device (which re-triggers approval) even if the session
# cache is wiped. If unset, a token is minted on first login and cached.
RH_DEVICE_TOKEN = os.getenv("RH_DEVICE_TOKEN", _get("rh.auth", "device"))

# --- Login flow ---
LOGIN_URL = os.getenv("LOGIN_URL", _get("login", "url"))

# --- /exec endpoint ---
EXEC_TOKEN = os.getenv("EXEC_TOKEN", _get("exec", "token"))
EXEC_TOKEN_SECRET = os.getenv("EXEC_TOKEN_SECRET", _get("exec", "token_secret"))
EXEC_TIMEOUT_SECONDS = int(os.getenv("EXEC_TIMEOUT_SECONDS", _get("exec", "timeout_seconds", "60")))
EXEC_REQUIRE_AUTH = os.getenv(
    "EXEC_REQUIRE_AUTH", _get("exec", "require_auth", "true")
).lower() == "true"

# --- Robinhood MCP (official hosted agentic-trading server) ---
# HTTP-transport MCP endpoint; OAuth Bearer required. Token is provisioned via
# the agentic-account OAuth flow (separate from our password session).
MCP_URL = os.getenv("MCP_URL", _get("mcp", "url", "https://agent.robinhood.com/mcp/trading"))
MCP_TOKEN = os.getenv("MCP_TOKEN", _get("mcp", "token"))
MCP_TOKEN_SECRET = os.getenv("MCP_TOKEN_SECRET", _get("mcp", "token_secret"))

# --- Server ---
PORT = int(os.getenv("PORT", _get("server", "port", "8080")))
DEBUG = os.getenv("FLASK_DEBUG", _get("server", "debug", "false")).lower() == "true"

# --- Session state (token cache on disk) ---
DEFAULT_PROFILE = os.getenv("DEFAULT_PROFILE", _get("rh.auth", "profile", "rh.auth"))
STATE_DIR = os.getenv(
    "STATE_DIR",
    _get("server", "state_dir", str(Path(__file__).resolve().parent / "state")),
)
