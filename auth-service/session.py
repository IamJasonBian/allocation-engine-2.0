"""Session manager — the concurrency-safe front door other services hit.

Implements the semantics from the design sketch, with threads instead of
asyncio (the server is a stdlib threaded http.server, so a Future-based
in-flight map is lighter than bolting on an event loop):

    get_session(profile_id):
        1. load persisted state; if the token is still valid, return it
        2. otherwise start (or join) a single in-flight auth for that profile
        3. caller-side timeout protects the whole wait

Three timeouts, mirroring the sketch:
    * caller-side       — CALLER_TIMEOUT (100s), on the whole get_session wait
    * login HTTP        — LOGIN_HTTP_TIMEOUT (10s), on each oauth2/token POST
    * approval deadline — APPROVAL_DEADLINE (90s), on the device-push wait
"""

import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import asdict
from pathlib import Path

import config
import robinhood
from gcp_secrets import get_secret
from models import AuthResult, Credentials, Session

log = logging.getLogger("session")

CALLER_TIMEOUT = 200
LOGIN_HTTP_TIMEOUT = 10
APPROVAL_DEADLINE = 180

# How often the cached token is checked against Robinhood rather than the
# clock. `expires_at` only says when the token *would* lapse; it says nothing
# about a server-side revocation. On 2026-08-04 Robinhood revoked the session
# ~18h before its stated expiry and every consumer 401'd for a day while
# /auth/status kept reporting authenticated — the clock check can't see that.
# Verifying costs one GET /user/, so do it periodically, not per vend.
VERIFY_INTERVAL = int(os.getenv("TOKEN_VERIFY_INTERVAL", "300"))

_verify_lock = threading.Lock()
# profile_id -> (access_token, monotonic_ts, ok)
_verified: dict[str, tuple[str, float, bool]] = {}


def _verify_cached(profile_id: str, session) -> bool:
    """Is `session` still good at Robinhood? Throttled per profile+token.

    A negative result is never cached: once a token fails we want the next
    caller to re-drive auth rather than wait out the interval.
    """
    if session is None or not session.access_token:
        return False
    now = time.monotonic()
    with _verify_lock:
        prev = _verified.get(profile_id)
        if (prev and prev[0] == session.access_token
                and prev[2] and now - prev[1] < VERIFY_INTERVAL):
            return True

    ok = robinhood.check_token_valid(session, verify=True)
    with _verify_lock:
        if ok:
            _verified[profile_id] = (session.access_token, now, True)
        else:
            _verified.pop(profile_id, None)
    if not ok:
        log.warning("cached token for %s rejected by Robinhood despite "
                    "expires_at=%s — forcing re-auth",
                    profile_id, getattr(session, "expires_at", None))
    return ok


def _forget_verification(profile_id: str) -> None:
    with _verify_lock:
        _verified.pop(profile_id, None)

_STATE_DIR = Path(config.STATE_DIR)
_lock = threading.Lock()
_inflight: dict[str, Future] = {}

# Optional hook: called with (profile_id) right before we block on a device
# push, so the server can ping a human. Set by server.py.
on_approval_needed = None


# --------------------------------------------------------------------------- #
# state persistence (token cache)  — frozen Session <-> JSON on disk
# --------------------------------------------------------------------------- #

def _state_path(profile_id: str) -> Path:
    safe = profile_id.replace("/", "_")
    return _STATE_DIR / f"{safe}.json"


def load(profile_id: str) -> Session | None:
    path = _state_path(profile_id)
    if not path.exists():
        return None
    try:
        d = json.loads(path.read_text())
        return Session(**d)
    except Exception as e:  # noqa: BLE001 — corrupt cache shouldn't crash auth
        log.warning("failed to load state for %s: %s", profile_id, e)
        return None


def save(profile_id: str, session: Session) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(profile_id)
    path.write_text(json.dumps(asdict(session)))
    path.chmod(0o600)


def _device_token(profile_id: str) -> str:
    """Resolve the device token, most-stable source first.

    1. pinned config value (RH_DEVICE_TOKEN / [rh.auth] device) — never changes
    2. the token cached from a prior login
    3. a freshly minted UUID (first ever login)
    """
    if config.RH_DEVICE_TOKEN:
        return config.RH_DEVICE_TOKEN
    existing = load(profile_id)
    if existing and existing.device_token:
        return existing.device_token
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# credentials from Secret Manager -> frozen dataclass
# --------------------------------------------------------------------------- #

def load_credentials(profile_id: str) -> Credentials:
    username = get_secret(config.SECRET_USERNAME, config.GCP_PROJECT_ID)
    password = get_secret(config.SECRET_PASSWORD, config.GCP_PROJECT_ID)
    totp = None
    if config.SECRET_TOTP:
        totp = get_secret(config.SECRET_TOTP, config.GCP_PROJECT_ID)
    return Credentials(
        username=username,
        password=password,
        totp_secret=totp,
        device_token=_device_token(profile_id),
    )


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #

def _do_auth(profile_id: str) -> AuthResult:
    # Token reuse: if we have a cached refresh token, spend it first — that
    # renews the access token silently, with no device push. Only fall back to
    # a full login (which prompts the human) if refresh fails or is absent.
    cached = load(profile_id)
    if cached and cached.refresh_token:
        refreshed = robinhood.refresh(cached)
        if refreshed.status == "OK" and refreshed.session is not None:
            save(profile_id, refreshed.session)
            log.info("token refreshed for %s (no push)", profile_id)
            return refreshed
        log.warning("refresh failed for %s (%s) — falling back to full login",
                    profile_id, refreshed.error_code)

    creds = load_credentials(profile_id)
    if callable(on_approval_needed):
        # We can't know for sure a push is coming until the server responds, but
        # the common prod path (trusted device, no TOTP) always prompts — so we
        # notify up front and the human ignores it if not needed.
        try:
            on_approval_needed(profile_id)
        except Exception:  # noqa: BLE001
            pass
    result = robinhood.authenticate(
        creds, http_timeout=LOGIN_HTTP_TIMEOUT, approval_deadline=APPROVAL_DEADLINE,
    )
    if result.status == "OK" and result.session is not None:
        save(profile_id, result.session)
    else:
        log.error("auth failed for %s: %s / %s", profile_id,
                  result.error_code, result.detail)
    return result


def get_session(profile_id: str = "rh.auth", force: bool = False) -> AuthResult:
    """Return an authenticated session for `profile_id`.

    Fast path returns the cached token. `force=True` skips the cache and drives a
    real (re)authentication — used by the manual /login trigger so it can't be
    fooled by a locally-unexpired-but-server-revoked token. Otherwise a single
    auth runs per profile (concurrent callers join the same Future) under a
    caller-side timeout.
    """
    if not force:
        cached = load(profile_id)
        # Clock first (cheap), then Robinhood itself (throttled). A token can
        # be locally unexpired and still revoked server-side.
        if robinhood.check_token_valid(cached) and _verify_cached(profile_id, cached):
            return AuthResult("OK", session=cached)
    else:
        _forget_verification(profile_id)

    with _lock:
        task = _inflight.get(profile_id)
        if task is None:
            task = Future()
            _inflight[profile_id] = task
            starter = task
        else:
            starter = None

    if starter is not None:
        # This caller owns the auth; run it and fulfil the Future.
        def _run():
            try:
                starter.set_result(_do_auth(profile_id))
            except Exception as e:  # noqa: BLE001
                starter.set_exception(e)
            finally:
                with _lock:
                    _inflight.pop(profile_id, None)
        threading.Thread(target=_run, name=f"auth-{profile_id}", daemon=True).start()

    try:
        return task.result(timeout=CALLER_TIMEOUT)
    except FutureTimeout:
        return AuthResult("TIMEOUT", error_code="CALLER_TIMEOUT",
                          detail=f"auth did not complete within {CALLER_TIMEOUT}s")
    except Exception as e:  # noqa: BLE001
        return AuthResult("ERROR", error_code="AUTH_EXCEPTION", detail=str(e))


def status(profile_id: str = "rh.auth", verify: bool = True) -> dict:
    """Auth status for callers / alerting — never triggers a login.

    ``authenticated`` means Robinhood still accepts the token, not merely that
    the clock hasn't passed ``expires_at``. Reporting the clock is what let a
    revoked session look healthy for a day. ``token_verified`` distinguishes a
    confirmed-good token from one we only know is unexpired.
    """
    session = load(profile_id)
    unexpired = robinhood.check_token_valid(session)
    verified = _verify_cached(profile_id, session) if (verify and unexpired) else None
    return {
        "profile": profile_id,
        "authenticated": unexpired if verified is None else verified,
        "token_unexpired": unexpired,
        "token_verified": verified,
        "verify_interval": VERIFY_INTERVAL,
        "expires_at": session.expires_at if session else None,
        "account_number": session.account_number if session else None,
    }
