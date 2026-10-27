"""Probe a local, credential-less auth-service with the real AuthServiceClient.

Server is bound to 127.0.0.1 only; no RH credentials, so nothing can reach
Robinhood. Prints one JSON line per probe.
"""
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer

ROOT = sys.argv[1]
os.environ.update(EXEC_TOKEN="t", AUTH_SERVICE_ENV="/nonexistent",
                  STATE_DIR=sys.argv[2], EXEC_REQUIRE_AUTH="true")
sys.path[:0] = [os.path.join(ROOT, "auth-service")]
import server  # noqa: E402
import guardrails  # noqa: E402

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

# app-side client (module name `config` collides with auth-service's; load app after)
sys.path.insert(0, ROOT)
for m in ("config",):
    sys.modules.pop(m, None)
import requests  # noqa: E402
from app.auth_service_client import (  # noqa: E402
    AuthServiceClient, AuthServiceError, AuthServiceNotConfigured,
    build_replace_payload, walk_order)


def out(probe, **kw):
    print(json.dumps({"probe": probe, **kw}, default=str))


base = f"http://127.0.0.1:{port}"

# 1. https enforcement (real client, unpatched)
try:
    AuthServiceClient(base_url=base, token="t").get_token()
    out("ac-https-bearer-only", result="NO RAISE")
except AuthServiceNotConfigured as e:
    out("ac-https-bearer-only", raised="AuthServiceNotConfigured", msg=str(e)[:80])


class LocalClient(AuthServiceClient):
    """Real client, https check skipped so it can talk to the local box."""
    def _check_config(self):
        pass


c = LocalClient(base_url=base, token="t", timeout=5)

# 2. raw box responses for the routes the client calls
for method, path in [("GET", "/orders/abc"), ("POST", "/orders/abc/cancel"),
                     ("POST", "/orders/options/limit"), ("GET", "/health")]:
    r = requests.request(method, base + path, headers={"Authorization": "Bearer t"},
                         json={"dry_run": True}, timeout=5)
    out("route", method=method, path=path, status=r.status_code, body=r.json())

# 3. get_order -> 404 -> raises
try:
    c.get_order("abc")
    out("ac-get-order-route-missing", result="NO RAISE")
except AuthServiceError as e:
    out("ac-get-order-route-missing", raised=type(e).__name__, msg=str(e)[:90])

# 4. walk_order fails at first lookup
try:
    walk_order(c, "abc", 1.0, dry_run=False)
    out("ac-walk-live", result="NO RAISE")
except AuthServiceError as e:
    out("ac-walk-live", raised=type(e).__name__, msg=str(e)[:90])

# 5. cancel on a route that does not exist -> reported as success
out("ac-cancel-404-false-success", result=c.cancel_order("abc", dry_run=False))

# 6. unrelated garbage id also "succeeds"
out("ac-cancel-404-false-success:any-id",
    result=c.cancel_order("definitely-not-an-order", dry_run=False))

# 7. replace of a payload built from a plain limit order -> guardrail
limit_order = {"account": "a", "instrument": "i", "symbol": "SPY", "type": "limit",
               "side": "sell", "time_in_force": "gfd", "trigger": "immediate",
               "quantity": "1", "price": "500"}
p = build_replace_payload(limit_order, price=501)
out("ac-replace-trailing-only:guardrail", reason=guardrails.check_trailing_stop_payload(p))
r = requests.post(base + "/orders/trailing_stop/replace", headers={"Authorization": "Bearer t"},
                  json={"order_id": "x", "payload": p, "dry_run": True}, timeout=10)
out("ac-replace-trailing-only:route", status=r.status_code, body=r.json())

# 8. DIRECT_ALLOW_LIST not consulted: a non-listed path still gets sent
sent = []
orig = requests.request
requests.request = lambda m, u, **k: (sent.append((m, u.replace(base, ""))), orig(m, u, **k))[1]
try:
    c.get_option_positions()
except AuthServiceError as e:
    pass
requests.request = orig
out("ac-direct-allowlist-unenforced", sent=sent,
    in_allow_list=[s in AuthServiceClient.DIRECT_ALLOW_LIST for s in sent])

# 9. unauthenticated caller
r = requests.post(base + "/orders/abc/cancel", timeout=5)
out("box-unauth-cancel", status=r.status_code, body=r.json())

httpd.shutdown()
