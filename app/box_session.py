"""Robinhood session token vended by the auth-service box, cached in sqlite.

All auth flows route through the box (GET /token): the box owns the RH
credentials, device identity, and refresh loop. This module is the app-side
cache — the same sqlite file as the stop-sweeper queue — so a fresh token is
one local read, and the box is only called when the cached token is missing,
near expiry, or a refresh is forced.

Auth calls are exempt from the destructive-action gates: those gates guard
order payloads and MCP tool calls, not authentication.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_META_KEY = "rh_box_token"
_EXPIRY_LEAD_SECONDS = 300  # refresh when within 5 minutes of expiry

_SCHEMA = "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"


def _db_path():
    from app.config import Config
    return Config.STOP_DB_PATH


def _ensure_dir(path):
    import os
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def _read_meta(path, key):
    # Short-lived connection per call — safe across Flask/engine threads.
    # Ensure the parent dir exists (ephemeral FS: data/ may not exist yet).
    _ensure_dir(path)
    with sqlite3.connect(path) as db:
        db.execute(_SCHEMA)
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _write_meta(path, key, value):
    _ensure_dir(path)
    with sqlite3.connect(path) as db:
        db.execute(_SCHEMA)
        db.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, value))


def _expires_epoch(token_info) -> float | None:
    """Normalize expires_at (epoch number or ISO string) to epoch seconds."""
    exp = (token_info or {}).get("expires_at")
    if exp is None:
        return None
    if isinstance(exp, (int, float)):
        return float(exp)
    try:
        return float(exp)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def token_expiring(token_info, lead_seconds=_EXPIRY_LEAD_SECONDS) -> bool:
    """True when the token is missing an expiry or within the refresh window."""
    exp = _expires_epoch(token_info)
    if exp is None:
        return True  # unknown lifetime — treat as stale, let the box decide
    return exp - time.time() <= lead_seconds


def get_cached_token(db_path=None):
    raw = _read_meta(db_path or _db_path(), _META_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def get_box_token(force=False, client=None, db_path=None):
    """Return a live token dict {token, token_type, expires_at, account_number}.

    sqlite-first: a cached, unexpired token is returned without touching the
    box. Returns None (never raises) when the box can't vend one — callers
    treat that as "not authenticated" and retry next tick.
    """
    path = db_path or _db_path()
    if not force:
        cached = get_cached_token(path)
        if cached and cached.get("token") and not token_expiring(cached):
            return cached

    from app.auth_service_client import (
        AuthServiceClient, AuthServiceError, OTPRequired,
    )
    client = client or AuthServiceClient()
    try:
        tok = client.get_token()
    except OTPRequired as e:
        # AuthServiceClient already logged "OTP needed"
        log.warning("[box-session] token blocked pending OTP/approval: %s", e)
        return None
    except AuthServiceError as e:
        log.warning("[box-session] token fetch failed (box /token missing or "
                    "unreachable?): %s", e)
        return None
    if not tok.get("token"):
        log.warning("[box-session] box returned no token: %s", tok)
        return None
    _write_meta(path, _META_KEY, json.dumps(tok))
    log.info("[box-session] cached fresh RH token from box "
             "(account=%s, expires_at=%s)",
             tok.get("account_number"), tok.get("expires_at"))
    return tok


def cached_token_status(db_path=None) -> dict:
    """Auth-status fragment derived purely from the sqlite cache."""
    tok = get_cached_token(db_path or _db_path())
    if not tok:
        return {"box_token_cached": False}
    return {
        "box_token_cached": True,
        "box_token_expires_at": tok.get("expires_at"),
        "box_token_expiring": token_expiring(tok),
        "account_number": tok.get("account_number", ""),
    }
