"""Robinhood MCP agentic OAuth — load, refresh, persist.

The MCP OAuth bundle in Secret Manager is JSON:
  access_token, refresh_token, client_id, token_endpoint, resource, scope, expires_at

Bare JWT secrets are still accepted; refresh is skipped when metadata is absent.
"""

import json
import logging
import threading
import time

import requests

import config
from gcp_secrets import add_secret_version, get_secret

log = logging.getLogger("mcp_oauth")

_lock = threading.Lock()


def parse_blob(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw.startswith("{"):
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def access_token_from_raw(raw: str) -> str:
    obj = parse_blob(raw)
    if obj:
        token = obj.get("access_token")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return raw.strip()


def expires_at(obj: dict) -> float | None:
    exp = obj.get("expires_at")
    if isinstance(exp, (int, float)):
        return float(exp)
    return None


def needs_refresh(obj: dict, now: float | None = None) -> bool:
    exp = expires_at(obj)
    if exp is None:
        return False
    now = time.time() if now is None else now
    return now >= exp - config.MCP_REFRESH_LEAD_SECONDS


def refresh_blob(obj: dict, http_timeout: int = 15) -> dict:
    refresh_token = obj.get("refresh_token")
    client_id = obj.get("client_id")
    token_endpoint = obj.get("token_endpoint")
    if not refresh_token or not client_id or not token_endpoint:
        raise ValueError("MCP OAuth blob missing refresh_token, client_id, or token_endpoint")

    payload: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": str(refresh_token),
        "client_id": str(client_id),
    }
    scope = obj.get("scope")
    if scope:
        payload["scope"] = str(scope)
    resource = obj.get("resource")
    if resource:
        payload["resource"] = str(resource)

    try:
        resp = requests.post(str(token_endpoint), json=payload, timeout=http_timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"MCP OAuth refresh HTTP error: {e}") from e

    data = resp.json()
    access = data.get("access_token")
    if not access:
        raise RuntimeError(f"MCP OAuth refresh missing access_token: {data}")

    updated = dict(obj)
    updated["access_token"] = access
    if data.get("refresh_token"):
        updated["refresh_token"] = data["refresh_token"]
    if data.get("expires_in"):
        updated["expires_at"] = time.time() + int(data["expires_in"])
    elif data.get("expires_at"):
        updated["expires_at"] = float(data["expires_at"])
    return updated


def _persist(secret_name: str, obj: dict) -> None:
    add_secret_version(secret_name, json.dumps(obj), config.GCP_PROJECT_ID)


def get_access_token() -> str:
    """Return a valid MCP bearer, refreshing the Secret Manager blob when needed."""
    if config.MCP_TOKEN_SECRET:
        return _from_secret(config.MCP_TOKEN_SECRET, persist=True)
    if config.MCP_TOKEN:
        return _from_raw(config.MCP_TOKEN, persist=False)
    return ""


def _from_raw(raw: str, *, persist: bool) -> str:
    obj = parse_blob(raw)
    if obj is None:
        return raw.strip()
    if needs_refresh(obj):
        log.info("MCP OAuth token near expiry — refreshing")
        obj = refresh_blob(obj)
        if persist:
            raise RuntimeError("MCP_TOKEN literal cannot be persisted after refresh")
        log.warning("MCP OAuth refreshed in memory only ([mcp] token is literal)")
    return access_token_from_raw(json.dumps(obj))


def _from_secret(secret_name: str, *, persist: bool) -> str:
    with _lock:
        raw = get_secret(secret_name, config.GCP_PROJECT_ID)
        obj = parse_blob(raw)
        if obj is None:
            return raw.strip()
        if needs_refresh(obj):
            log.info("MCP OAuth token near expiry — refreshing")
            obj = refresh_blob(obj)
            if persist:
                try:
                    _persist(secret_name, obj)
                    log.info("MCP OAuth token refreshed and saved to %s", secret_name)
                except Exception:
                    log.exception(
                        "MCP OAuth refreshed but failed to persist to %s — "
                        "re-OAuth may be required after restart",
                        secret_name,
                    )
        return access_token_from_raw(json.dumps(obj))


def refresh_once() -> None:
    """One background tick: timestamp-check the secret and refresh if due.

    Errors are logged, never raised — a failed refresh must not kill the loop,
    and a dead chain (invalid_grant / revoked) needs a human re-login anyway.
    """
    if not config.MCP_TOKEN_SECRET:
        return
    try:
        _from_secret(config.MCP_TOKEN_SECRET, persist=True)
    except Exception:
        log.exception("MCP OAuth background refresh failed")


def start_refresh_loop(interval: float | None = None) -> threading.Thread | None:
    """Start the daemon thread that keeps the refresh chain alive while idle.

    Args:
        interval: Seconds between ticks; defaults to MCP_REFRESH_INTERVAL_SECONDS.

    Returns:
        The started thread, or None when no Secret Manager token is configured.
    """
    if not config.MCP_TOKEN_SECRET:
        return None
    interval = config.MCP_REFRESH_INTERVAL_SECONDS if interval is None else interval

    def _run() -> None:
        while True:
            refresh_once()
            time.sleep(interval)

    t = threading.Thread(target=_run, name="mcp-oauth-refresh", daemon=True)
    t.start()
    log.info("MCP OAuth refresh loop started (every %ss, lead %ss)",
             interval, config.MCP_REFRESH_LEAD_SECONDS)
    return t
