"""Relay calls to the official Robinhood MCP server (HTTP transport).

The MCP endpoint (https://agent.robinhood.com/mcp/trading) speaks JSON-RPC 2.0
over streamable HTTP and requires an OAuth Bearer token. This module forwards a
caller-supplied JSON-RPC payload and relays the response + status code, so other
services can drive the MCP through our box.

The OAuth token is provisioned separately (agentic-account OAuth flow) and lives
in [mcp] token / MCP_TOKEN (or a Secret Manager name via MCP_TOKEN_SECRET).
Without it the MCP returns 401, which we relay unchanged.
"""

import logging

import requests

import config

log = logging.getLogger("mcp")


def relay(payload: dict, token: str | None = None,
          session_id: str | None = None, timeout: int = 30) -> dict:
    """Forward one JSON-RPC payload to the MCP and relay the outcome + codes."""
    if not config.MCP_URL:
        return {"ok": False, "error_code": "MCP_URL_UNSET"}
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    try:
        r = requests.post(config.MCP_URL, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        return {"ok": False, "error_code": "MCP_HTTP_ERROR", "detail": str(e)}

    out = {
        "ok": r.ok,
        "status": r.status_code,
        "session_id": r.headers.get("Mcp-Session-Id"),
    }
    # MCP may answer as JSON or as an SSE stream — capture whichever we get.
    try:
        out["result"] = r.json()
    except ValueError:
        out["body"] = (r.text or "")[:2000]
    if not r.ok:
        out["error_code"] = f"MCP_HTTP_{r.status_code}"
    return out
