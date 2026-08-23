#!/usr/bin/env python3
"""Mint a fresh Robinhood MCP OAuth bundle (desktop browser flow) for Secret Manager.

Run from a laptop, not the box: Robinhood only authenticates agents on a desktop
browser. Prints the JSON bundle mcp_oauth.py expects (access_token, refresh_token,
client_id, token_endpoint, resource, scope, expires_at) to stdout; everything else
goes to stderr. Pipe it straight into Secret Manager so the token never lands in a
shell history:

    python3 mcp_login.py | gcloud secrets versions add rh-mcp-oauth-token \
        --project route-manager-prod --data-file=-

Needed only to bootstrap, or after the refresh chain dies (invalid_grant / token
revoked / persist failed mid-rotate). The box refreshes on its own otherwise —
and it must be the ONLY refresher: refresh tokens are single-use.
"""
import base64, hashlib, json, secrets, sys, time, urllib.parse, urllib.request, webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

RESOURCE = "https://agent.robinhood.com/mcp/trading"
META = "https://agent.robinhood.com/.well-known/oauth-authorization-server"
PORT = 48721
REDIRECT = f"http://localhost:{PORT}/callback"

def log(m): print(m, file=sys.stderr, flush=True)

def post_json(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r: return json.load(r)

def post_form(url, body):
    req = urllib.request.Request(url, urllib.parse.urlencode(body).encode(),
                                 {"content-type": "application/x-www-form-urlencoded", "accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return json.load(r)
    except urllib.error.HTTPError as e:
        log(f"token endpoint {e.code}: {e.read().decode()[:500]}"); raise

meta = json.load(urllib.request.urlopen(META, timeout=20))
reg = post_json(meta["registration_endpoint"], {
    "client_name": "allocation-engine-auth-service",
    "redirect_uris": [REDIRECT],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
})
client_id = reg["client_id"]
log(f"registered client_id={client_id}")

verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(16)
auth_url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
    "scope": "internal", "state": state, "code_challenge": challenge,
    "code_challenge_method": "S256", "resource": RESOURCE,
})

result = {}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q and q.get("state", [None])[0] == state:
            result["code"] = q["code"][0]; msg = b"Authorized. You can close this tab."
        else:
            result["error"] = q; msg = b"Authorization failed; see terminal."
        self.send_response(200); self.end_headers(); self.wfile.write(msg)

srv = HTTPServer(("localhost", PORT), H); srv.timeout = 300
log("opening browser for Robinhood login..."); log(auth_url)
webbrowser.open(auth_url)
while "code" not in result and "error" not in result: srv.handle_request()
if "error" in result: log(f"callback error: {result['error']}"); sys.exit(1)

tok = post_form(meta["token_endpoint"], {
    "grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT,
    "client_id": client_id, "code_verifier": verifier, "resource": RESOURCE,
})
out = {
    "client_id": client_id,
    "token_endpoint": meta["token_endpoint"],
    "resource": RESOURCE,
    "refresh_token": tok.get("refresh_token"),
    "access_token": tok.get("access_token"),
    "expires_at": int(time.time()) + int(tok.get("expires_in", 0)),
    "scope": tok.get("scope"),
}
log(f"got token: refresh={'yes' if out['refresh_token'] else 'NO'} expires_in={tok.get('expires_in')}")
sys.stdout.write(json.dumps(out))
