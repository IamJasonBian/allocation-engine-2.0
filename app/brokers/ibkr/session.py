"""IBKR session lifecycle — builds the ibind OAuth 1.0a client and manages the
live-session-token handshake, brokerage session init, and tickle keepalive.

ibind (``pip install ibind[oauth]``) performs the RSA / Diffie-Hellman live
session token handshake internally when ``use_oauth=True``. We layer a thin
``ensure_auth`` / ``tickle`` / ``auth_status`` lifecycle on top so every broker
method can lazily (re)establish a brokerage session before issuing requests.
"""

import logging
import time

import ibind
from ibind import IbkrClient
from ibind.oauth.oauth1a import OAuth1aConfig

log = logging.getLogger(__name__)

# Live session token lifetime is ~24h. Refresh the brokerage session a little
# before that to avoid edge-of-expiry failures.
_SESSION_TTL_SECONDS = 23 * 60 * 60


def _result_data(result):
    """Extract the payload from an ibind Result (or pass through a raw dict/list)."""
    if result is None:
        return None
    data = getattr(result, "data", None)
    if data is not None:
        return data
    # Some monkeypatched/test doubles return the payload directly.
    if isinstance(result, (dict, list)):
        return result
    return data


class IBKRSession:
    """Owns the ibind client and the OAuth/brokerage-session lifecycle."""

    def __init__(
        self,
        account_id: str,
        *,
        paper: bool = True,
        consumer_key: str = "",
        access_token: str = "",
        access_token_secret: str = "",
        dh_prime: str = "",
        signature_key_path: str = "",
        encryption_key_path: str = "",
    ):
        self.account_id = account_id
        self.paper = paper
        self._authenticated = False
        self._session_started_at: float | None = None

        oauth_config = OAuth1aConfig(
            consumer_key=consumer_key,
            access_token=access_token,
            access_token_secret=access_token_secret,
            dh_prime=dh_prime,
            signature_key_fp=signature_key_path or None,
            encryption_key_fp=encryption_key_path or None,
        )
        self.client = IbkrClient(
            account_id=account_id,
            use_oauth=True,
            oauth_config=oauth_config,
        )

    # -- lifecycle ----------------------------------------------------------

    def ensure_auth(self):
        """Ensure a live brokerage session exists, (re)initialising if needed.

        Establishes the brokerage session via ``/iserver/auth/ssodh/init`` and
        keeps it alive. Tolerates transient errors so a single blip does not
        force a full re-handshake.
        """
        if self._authenticated and not self._session_expired():
            # Cheap keepalive; ignore transient failures.
            try:
                self.tickle()
                return
            except Exception:
                log.warning("[ibkr] tickle failed during ensure_auth — re-initialising session")
                self._authenticated = False

        self._init_brokerage_session()

    def _session_expired(self) -> bool:
        if self._session_started_at is None:
            return True
        return (time.time() - self._session_started_at) >= _SESSION_TTL_SECONDS

    def _init_brokerage_session(self):
        """POST /iserver/auth/ssodh/init to open a brokerage session."""
        try:
            result = self.client.post(
                "iserver/auth/ssodh/init",
                params={"publish": True, "compete": True},
            )
            data = _result_data(result) or {}
            authenticated = bool(data.get("authenticated", True)) if isinstance(data, dict) else True
            self._authenticated = authenticated
            self._session_started_at = time.time()
            log.info("[ibkr] Brokerage session initialised (authenticated=%s)", authenticated)
        except Exception as e:
            self._authenticated = False
            log.error("[ibkr] Failed to initialise brokerage session: %s", e)
            raise

    def tickle(self):
        """GET /tickle keepalive. Returns the parsed payload."""
        result = self.client.tickle()
        return _result_data(result)

    def auth_status(self) -> dict:
        """Return standardized auth status: authenticated, session_expires_at, account_id."""
        authenticated = self._authenticated
        try:
            result = self.client.get("iserver/auth/status")
            data = _result_data(result)
            if isinstance(data, dict) and "authenticated" in data:
                authenticated = bool(data["authenticated"])
                self._authenticated = authenticated
        except Exception as e:
            log.warning("[ibkr] auth_status check failed: %s", e)

        expires_at = None
        if self._session_started_at is not None:
            expires_at = self._session_started_at + _SESSION_TTL_SECONDS

        return {
            "authenticated": authenticated,
            "session_expires_at": expires_at,
            "account_id": self.account_id,
        }
