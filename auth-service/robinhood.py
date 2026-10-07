"""Robinhood API client — auth flow + percentage trailing-stop orders.

Scope is deliberately narrow (per the service's mandate):
  * authenticate (password grant + device-approval / MFA)
  * read active percentage trailing-stop orders
  * place / replace percentage trailing-stop orders

The exact request/response shapes are modelled on robin_stocks, which may be
stale — so every network step logs its raw JSON (truncated) at DEBUG so we can
confirm against the live API and adjust. Nothing here mutates account state
unless dry_run=False is passed explicitly.
"""

import json
import logging
import time
import uuid

import pyotp
import requests

from models import AuthResult, Credentials, Session

log = logging.getLogger("robinhood")

BASE = "https://api.robinhood.com"
# Robinhood's long-standing public OAuth client id (same one robin_stocks uses).
CLIENT_ID = "c82SH0WZOsabOXGP2sxqcj34FxkvfnWRZBKlBjFS"

_BASE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "auth-service/1.0 (+robinhood)",
    "X-Robinhood-API-Version": "1.431.4",
}

ACTIVE_STATES = {"queued", "confirmed", "unconfirmed", "partially_filled"}


_SENSITIVE = {
    "access_token", "refresh_token", "password", "mfa_code", "backup_code",
    "read_only_secondary_access_token", "device_token", "bearer_token",
}


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if k in _SENSITIVE else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _dump(label: str, resp: requests.Response) -> dict:
    """Log a truncated, token-redacted raw response and return parsed JSON."""
    try:
        data = resp.json()
    except ValueError:
        log.debug("%s -> HTTP %s: %s", label, resp.status_code, (resp.text or "")[:800])
        return {}
    log.debug("%s -> HTTP %s: %s", label, resp.status_code,
              json.dumps(_redact(data))[:1000])
    return data


def _new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_BASE_HEADERS)
    return s


# --------------------------------------------------------------------------- #
# token validity
# --------------------------------------------------------------------------- #

def check_token_valid(session: Session | None, skew: int = 60, verify: bool = False) -> bool:
    """True if `session` looks usable.

    Local expiry check by default (fast). With verify=True, also confirms the
    token against the live API (GET /user/), which is the real "is the token on
    the device still good" test.
    """
    if session is None or not session.access_token:
        return False
    if session.expires_at and session.expires_at - skew <= time.time():
        return False
    if not verify:
        return True
    try:
        s = _new_session()
        s.headers.update(session.headers())
        r = s.get(f"{BASE}/user/", timeout=10)
        return r.status_code == 200
    except requests.RequestException:
        return False


# --------------------------------------------------------------------------- #
# auth flow
# --------------------------------------------------------------------------- #

def authenticate(creds: Credentials, http_timeout: int = 10,
                 approval_deadline: int = 90) -> AuthResult:
    """Full password-grant flow, handling device-approval + MFA.

    Returns an AuthResult; on success .session is a frozen Session. Errors carry
    a stable error_code so callers can alert a human.
    """
    device_token = creds.device_token or str(uuid.uuid4())
    http = _new_session()
    payload = {
        "client_id": CLIENT_ID,
        "expires_in": 86400,
        "grant_type": "password",
        "password": creds.password,
        "scope": "internal",
        "username": creds.username,
        "device_token": device_token,
        "try_passkeys": False,
        "token_request_path": "/login",
        "create_read_only_secondary_token": True,
    }
    if creds.totp_secret:
        payload["mfa_code"] = pyotp.TOTP(creds.totp_secret).now()

    try:
        resp = http.post(f"{BASE}/oauth2/token/", json=payload, timeout=http_timeout)
    except requests.Timeout:
        return AuthResult("TIMEOUT", error_code="LOGIN_HTTP_TIMEOUT",
                          detail=f"oauth2/token timed out after {http_timeout}s")
    except requests.RequestException as e:
        return AuthResult("ERROR", error_code="LOGIN_HTTP_ERROR", detail=str(e))

    data = _dump("oauth2/token", resp)

    # Device-approval workflow (the "approve on your phone" path).
    if data.get("verification_workflow"):
        workflow_id = data["verification_workflow"]["id"]
        approved = mfa_flow(http, device_token, workflow_id, deadline=approval_deadline)
        if not approved.ok:
            return AuthResult(approved.status, error_code=approved.error_code,
                              detail=approved.detail, approval_id=workflow_id)
        # Retry the token request now that the challenge is satisfied.
        try:
            resp = http.post(f"{BASE}/oauth2/token/", json=payload, timeout=http_timeout)
        except requests.RequestException as e:
            return AuthResult("ERROR", error_code="LOGIN_RETRY_ERROR", detail=str(e))
        data = _dump("oauth2/token (retry)", resp)

    # Classic TOTP/SMS MFA (only if no TOTP secret was configured).
    if data.get("mfa_required"):
        return AuthResult("MFA_REQUIRED", error_code="MFA_CODE_REQUIRED",
                          detail=f"mfa_type={data.get('mfa_type')}")

    access = data.get("access_token")
    if not access:
        return AuthResult("ERROR", error_code="LOGIN_NO_TOKEN",
                          detail=json.dumps(data)[:300])

    session = Session(
        access_token=access,
        refresh_token=data.get("refresh_token", ""),
        token_type=data.get("token_type", "Bearer"),
        expires_at=time.time() + int(data.get("expires_in", 86400)),
        device_token=device_token,
    )
    # Attach the account (needed for order placement).
    try:
        acct = get_account(session)
        session = session.with_account(acct.get("url", ""), acct.get("account_number", ""))
    except Exception as e:  # noqa: BLE001 — non-fatal, auth still succeeded
        log.warning("account lookup failed post-login: %s", e)

    return AuthResult("OK", session=session)


def refresh(session: Session, http_timeout: int = 10) -> AuthResult:
    """Renew the access token using the refresh token — no device push.

    This is the token-reuse path: when the cached access token expires we spend
    the refresh token instead of re-running the full login (which would prompt
    the human again). Account info carries over unchanged.
    """
    if not session.refresh_token:
        return AuthResult("ERROR", error_code="NO_REFRESH_TOKEN")
    http = _new_session()
    payload = {
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": session.refresh_token,
        "scope": "internal",
        "expires_in": 86400,
        "device_token": session.device_token,
    }
    try:
        resp = http.post(f"{BASE}/oauth2/token/", json=payload, timeout=http_timeout)
    except requests.RequestException as e:
        return AuthResult("ERROR", error_code="REFRESH_HTTP_ERROR", detail=str(e))
    data = _dump("oauth2/token (refresh)", resp)
    access = data.get("access_token")
    if not access:
        return AuthResult("ERROR", error_code="REFRESH_FAILED",
                          detail=json.dumps(_redact(data))[:200])
    new = Session(
        access_token=access,
        refresh_token=data.get("refresh_token", session.refresh_token),
        token_type=data.get("token_type", "Bearer"),
        expires_at=time.time() + int(data.get("expires_in", 86400)),
        device_token=session.device_token,
        account_url=session.account_url,
        account_number=session.account_number,
    )
    return AuthResult("OK", session=new)


class _ApprovalOutcome:
    def __init__(self, ok, status="OK", error_code=None, detail=""):
        self.ok = ok
        self.status = status
        self.error_code = error_code
        self.detail = detail


def mfa_flow(http: requests.Session, device_token: str, workflow_id: str,
             deadline: int = 90, poll_interval: float = 3.0) -> _ApprovalOutcome:
    """Drive the pathfinder device-approval challenge to completion.

    Unlike robin_stocks' unbounded `while True`, this honours `deadline` so a
    never-approved push can't wedge the service (see CLAUDE.md 429 storm note).
    """
    try:
        r = http.post(f"{BASE}/pathfinder/user_machine/", json={
            "device_id": device_token, "flow": "suv",
            "input": {"workflow_id": workflow_id},
        }, timeout=10)
        machine = _dump("pathfinder/user_machine", r)
        machine_id = machine.get("id")
        if not machine_id:
            return _ApprovalOutcome(False, "ERROR", "APPROVAL_NO_MACHINE_ID",
                                    json.dumps(machine)[:200])

        view_url = f"{BASE}/pathfinder/inquiries/{machine_id}/user_view/"
        deadline_ts = time.time() + deadline
        while time.time() < deadline_ts:
            v = _dump("inquiries/user_view", http.get(view_url, timeout=10))
            challenge = (v.get("type_context", {}) or {}).get("context", {}) \
                         .get("sheriff_challenge", {})
            ctype = challenge.get("type")
            status = challenge.get("status")

            if status == "validated":
                return _ApprovalOutcome(True)

            if ctype == "prompt":
                # Poll the push-approval status until the user taps Approve.
                cid = challenge.get("id")
                status_url = f"{BASE}/push/{cid}/get_prompts_status/"
                while time.time() < deadline_ts:
                    ps = _dump("push/get_prompts_status",
                               http.get(status_url, timeout=10))
                    if ps.get("challenge_status") == "validated":
                        # Tell pathfinder the challenge is satisfied.
                        http.post(view_url, json={"sequence": 0, "user_input": {"status": "continue"}}, timeout=10)
                        return _ApprovalOutcome(True)
                    log.info("waiting for device approval… (tap Approve on your phone)")
                    time.sleep(poll_interval)
                return _ApprovalOutcome(False, "TIMEOUT", "APPROVAL_TIMEOUT",
                                        "device push not approved before deadline")

            if ctype in ("sms", "email"):
                return _ApprovalOutcome(False, "MFA_REQUIRED", "MFA_CODE_REQUIRED",
                                        f"challenge type {ctype} requires a code")
            time.sleep(poll_interval)

        return _ApprovalOutcome(False, "TIMEOUT", "APPROVAL_TIMEOUT",
                                "no challenge resolution before deadline")
    except requests.RequestException as e:
        return _ApprovalOutcome(False, "ERROR", "APPROVAL_HTTP_ERROR", str(e))


# --------------------------------------------------------------------------- #
# account + orders
# --------------------------------------------------------------------------- #

def get_account(session: Session) -> dict:
    http = _new_session()
    http.headers.update(session.headers())
    r = http.get(f"{BASE}/accounts/", timeout=10)
    data = _dump("accounts", r)
    results = data.get("results", [])
    if not results:
        raise RuntimeError("no accounts returned")
    return results[0]


def get_trailing_stop_orders(session: Session, only_percentage: bool = True) -> list[dict]:
    """Return active trailing-stop orders (percentage type by default)."""
    http = _new_session()
    http.headers.update(session.headers())
    orders, url = [], f"{BASE}/orders/"
    while url:
        data = _dump("orders", http.get(url, timeout=15))
        orders.extend(data.get("results", []))
        url = data.get("next")

    out = []
    for o in orders:
        if o.get("state") not in ACTIVE_STATES:
            continue
        if o.get("trigger") != "stop":
            continue
        peg = o.get("trailing_peg") or {}
        # Read is tolerant: if the live shape differs we still surface the order.
        if only_percentage and peg.get("type") not in (None, "percentage"):
            continue
        out.append(o)
    return out


def build_trailing_stop_payload(*, account_url: str, instrument_url: str, symbol: str,
                                side: str, quantity, trail_percent, stop_price=None,
                                time_in_force: str = "gtc") -> dict:
    """Construct a percentage trailing-stop order payload.

    Uses the `trailing_peg` shape (robin_stocks). We confirm this against a live
    read before ever POSTing — see get_trailing_stop_orders.
    """
    payload = {
        "account": account_url,
        "instrument": instrument_url,
        "symbol": symbol,
        "type": "market",
        "time_in_force": time_in_force,
        "trigger": "stop",
        "side": side,
        "quantity": str(quantity),
        "trailing_peg": {"type": "percentage", "percentage": str(trail_percent)},
        "ref_id": str(uuid.uuid4()),
    }
    if stop_price is not None:
        payload["stop_price"] = str(stop_price)
    return payload


def place_trailing_stop(session: Session, payload: dict, dry_run: bool = True) -> dict:
    if dry_run:
        log.info("[dry_run] would POST %s/orders/ %s", BASE, json.dumps(payload))
        return {"dry_run": True, "method": "POST", "url": f"{BASE}/orders/", "payload": payload}
    http = _new_session()
    http.headers.update(session.headers())
    return _dump("orders (place)", http.post(f"{BASE}/orders/", json=payload, timeout=15))


def replace_trailing_stop(session: Session, order_id: str, payload: dict,
                          dry_run: bool = True) -> dict:
    """Replace an existing order via Robinhood's replace endpoint."""
    url = f"{BASE}/orders/{order_id}/replace/"
    if dry_run:
        log.info("[dry_run] would POST %s %s", url, json.dumps(payload))
        return {"dry_run": True, "method": "POST", "url": url, "payload": payload}
    http = _new_session()
    http.headers.update(session.headers())
    return _dump("orders (replace)", http.post(url, json=payload, timeout=15))


# --------------------------------------------------------------------------- #
# options positions + orders (read-only; for engine take-profit sweep)
# --------------------------------------------------------------------------- #

_OPTION_OPEN_STATES = ACTIVE_STATES | {"pending"}


def _fetch_option_instrument(http: requests.Session, option_url: str,
                             cache: dict) -> dict:
    if not option_url:
        return {}
    if option_url in cache:
        return cache[option_url]
    data = _dump("options/instrument", http.get(option_url, timeout=10))
    cache[option_url] = data if isinstance(data, dict) else {}
    return cache[option_url]


def _parse_rh_premium_cents(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_option_position(raw: dict, instrument: dict,
                            fill_entry_cents: float | None = None) -> dict | None:
    """Map RH options/positions row + instrument to engine-friendly fields.

    Take-profit entry is Robinhood's lot cost: ``average_price`` on the open
    position (premium per contract, cents). When that field is missing, an
    optional filled buy-to-open order premium may be supplied.
    """
    qty = float(raw.get("quantity", 0) or 0)
    if qty == 0:
        return None
    # RH `type` on positions is direction (long/short), not call/put.
    position_type = str(raw.get("type") or "long").lower()
    kind = str(instrument.get("type") or "").lower()
    if kind not in ("call", "put"):
        return None
    avg_cents = _parse_rh_premium_cents(raw.get("average_price"))
    entry_source = "position_average_price"
    if avg_cents <= 0 and fill_entry_cents and fill_entry_cents > 0:
        avg_cents = fill_entry_cents
        entry_source = "filled_open_order"
    elif avg_cents > 0 and fill_entry_cents and fill_entry_cents > 0:
        if abs(avg_cents - fill_entry_cents) > 1.0:
            log.info("option lot: position average_price=%s vs open-fill=%s "
                     "(using position lot for TP)",
                     avg_cents, fill_entry_cents)
    if avg_cents <= 0:
        return None
    avg_price = avg_cents / 100.0
    try:
        multiplier = float(raw.get("trade_value_multiplier", 100) or 100)
    except (TypeError, ValueError):
        multiplier = 100.0
    strike = instrument.get("strike_price")
    expiration = instrument.get("expiration_date") or ""
    chain_symbol = (raw.get("chain_symbol") or instrument.get("chain_symbol") or "")
    chain_symbol = str(chain_symbol).upper()
    if not chain_symbol or not strike or not expiration:
        return None
    cost_basis = round(qty * avg_price * multiplier, 2)
    return {
        "chain_symbol": chain_symbol,
        "option_type": kind,
        "position_type": position_type,
        "strike": float(strike),
        "expiration": str(expiration),
        "quantity": qty,
        "average_price": avg_cents,
        "purchase_price": round(avg_price, 4),
        "entry_source": entry_source,
        "trade_value_multiplier": multiplier,
        "cost_basis": cost_basis,
        "option_id": raw.get("option_id") or instrument.get("id") or "",
        "option_url": raw.get("option") or "",
        "source": "robinhood",
    }


def _build_filled_open_entry_map(http: requests.Session, orders: list[dict]) -> dict:
    """Map option instrument URL -> fill premium (cents) from buy-to-open fills."""
    entries: dict[str, float] = {}
    for o in orders:
        if not isinstance(o, dict) or o.get("state") != "filled":
            continue
        if str(o.get("direction") or "").lower() != "debit":
            continue
        # Option *order* prices are per-share premium dollars (position
        # average_price is per contract), so scale to the per-contract unit.
        prem_cents = _parse_rh_premium_cents(o.get("average_price") or o.get("price")) * 100.0
        if prem_cents <= 0:
            continue
        for leg in o.get("legs") or []:
            if str(leg.get("side") or "").lower() != "buy":
                continue
            if str(leg.get("position_effect") or "").lower() != "open":
                continue
            opt_url = leg.get("option") or ""
            if opt_url:
                entries[opt_url] = prem_cents
    return entries


def get_open_option_positions(session: Session) -> list[dict]:
    """Non-zero option positions; purchase price from RH lot average_price."""
    http = _new_session()
    http.headers.update(session.headers())
    inst_cache: dict = {}
    # Match robin_stocks: only open lots with quantity != 0.
    first_params = {"nonzero": "True"}
    all_orders: list[dict] = []
    orders_url = f"{BASE}/options/orders/"
    while orders_url:
        data = _dump("options/orders", http.get(orders_url, timeout=15))
        all_orders.extend(data.get("results", []))
        orders_url = data.get("next")
    fill_map = _build_filled_open_entry_map(http, all_orders)

    normalized, url = [], f"{BASE}/options/positions/"
    params = first_params
    while url:
        data = _dump("options/positions", http.get(url, params=params, timeout=15))
        params = None
        for raw in data.get("results", []):
            if not isinstance(raw, dict):
                continue
            opt_url = raw.get("option") or ""
            inst = _fetch_option_instrument(http, opt_url, inst_cache)
            fill_cents = fill_map.get(opt_url)
            row = normalize_option_position(raw, inst, fill_entry_cents=fill_cents)
            if row:
                normalized.append(row)
        url = data.get("next")
    return normalized


def _enrich_option_order_leg(http: requests.Session, leg: dict, chain_symbol: str,
                             inst_cache: dict) -> dict:
    out = dict(leg)
    inst = _fetch_option_instrument(http, leg.get("option", ""), inst_cache)
    out["chain_symbol"] = chain_symbol or inst.get("chain_symbol") or ""
    out["option_type"] = inst.get("type") or out.get("option_type") or ""
    if inst.get("strike_price") is not None:
        out["strike"] = float(inst["strike_price"])
    elif out.get("strike_price") is not None:
        out["strike"] = float(out["strike_price"])
    out["expiration"] = inst.get("expiration_date") or out.get("expiration_date") or ""
    return out


def get_option_orders(session: Session, *, open_only: bool = True) -> list[dict]:
    """Option orders from Robinhood; legs include contract identity for dedup."""
    http = _new_session()
    http.headers.update(session.headers())
    inst_cache: dict = {}
    orders, url = [], f"{BASE}/options/orders/"
    while url:
        data = _dump("options/orders", http.get(url, timeout=15))
        orders.extend(data.get("results", []))
        url = data.get("next")

    out = []
    for o in orders:
        if not isinstance(o, dict):
            continue
        if open_only and o.get("state") not in _OPTION_OPEN_STATES:
            continue
        chain = o.get("chain_symbol") or ""
        legs = [
            _enrich_option_order_leg(http, leg, chain, inst_cache)
            for leg in (o.get("legs") or [])
        ]
        enriched = dict(o)
        enriched["legs"] = legs
        enriched["limit_price"] = o.get("price")
        out.append(enriched)
    return out
