"""Relay calls to the official Robinhood MCP server (HTTP transport).

The MCP endpoint (https://agent.robinhood.com/mcp/trading) speaks JSON-RPC 2.0
over streamable HTTP and requires an OAuth Bearer token. This module forwards a
caller-supplied JSON-RPC payload and relays the response + status code, so other
services can drive the MCP through our box.

The OAuth token is provisioned separately (agentic-account OAuth flow) and lives
in [mcp] token / MCP_TOKEN (or a Secret Manager name via MCP_TOKEN_SECRET).
Without it the MCP returns 401, which we relay unchanged.
"""

import json
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


def parse_jsonrpc_result(relay_out: dict) -> dict | list | None:
    """Pull the JSON-RPC ``result`` from a relay response (JSON or SSE body)."""
    raw = relay_out.get("result")
    if isinstance(raw, dict):
        if "result" in raw:
            return raw["result"]
        if "error" in raw:
            return raw
        return raw
    body = relay_out.get("body") or ""
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk:
            continue
        try:
            msg = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if "result" in msg:
            return msg["result"]
        if "error" in msg:
            return msg
    return None


def unwrap_tool_content(parsed: dict | list | None):
    """MCP tools/call often wraps JSON in content[].text — decode when present."""
    if not isinstance(parsed, dict):
        return parsed
    content = parsed.get("content")
    if not isinstance(content, list) or not content:
        return parsed
    text = content[0].get("text") if isinstance(content[0], dict) else None
    if not isinstance(text, str):
        return parsed
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
