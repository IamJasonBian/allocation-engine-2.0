"""Drive RobinhoodTrader, IBKRTrader and AuthServiceClient against stubs.

No network to any broker: robin_stocks calls are monkeypatched, ib_async is a
fake module, and the "box" is a local 127.0.0.1 http server.
"""
import json
import logging
import os
import sys
import tempfile
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = sys.argv[1]
sys.path.insert(0, ROOT)
os.environ["STOP_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "stop.db")

logs = []


class Cap(logging.Handler):
    def emit(self, r):
        logs.append(f"{r.levelname} {r.name} {r.getMessage()}"[:160])


logging.getLogger().addHandler(Cap())
logging.getLogger().setLevel(logging.INFO)


def out(probe, **kw):
    print(json.dumps({"probe": probe, **kw}, default=str))


# ---------------- fake box ----------------
class Box(BaseHTTPRequestHandler):
    mode = "otp"

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if Box.mode == "otp":
            self._send(409, {"error": "not authenticated", "status": "PENDING",
                             "error_code": "DEVICE_APPROVAL_NEEDED"})
        elif Box.mode == "slow":
            time.sleep(3)
            self._send(200, {"token": "late"})
        elif Box.mode == "ok":
            self._send(200, {"token": "tok", "token_type": "Bearer",
                             "expires_at": time.time() + 3600, "account_number": "5QR"})

    do_POST = do_GET


httpd = ThreadingHTTPServer(("127.0.0.1", 0), Box)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_address[1]}"

from app import auth_service_client as asc  # noqa: E402
from app import box_session  # noqa: E402


class LocalClient(asc.AuthServiceClient):
    def _check_config(self):
        pass


# OTP detection precedes status code
Box.mode = "otp"
try:
    LocalClient(base_url=BASE, token="t", timeout=5).get_token()
except asc.AuthServiceError as e:
    out("ac-otp-detection", raised=type(e).__name__, is_otp=isinstance(e, asc.OTPRequired))
out("ac-auth-status-no-raise", result=LocalClient(base_url=BASE, token="t").auth_status())
out("ac-box-token-sqlite-cache:otp",
    result=box_session.get_box_token(client=LocalClient(base_url=BASE, token="t")))

# client timeout shorter than box wait -> None, retried next tick
Box.mode = "slow"
t0 = time.time()
r = box_session.get_box_token(force=True, client=LocalClient(base_url=BASE, token="t", timeout=1))
out("ac-client-timeout-vs-box-wait", result=r, waited_s=round(time.time() - t0, 1),
    log=[l for l in logs if "box-session" in l][-1:])

# ---------------- Robinhood ----------------
import robin_stocks.robinhood as rh  # noqa: E402

Box.mode = "ok"
os.environ.pop("AUTH_SERVICE_URL", None)
from app.config import Config  # noqa: E402

# init with box unconfigured -> nonfatal
Config.AUTH_SERVICE_URL = ""
from app.brokers.robinhood_client import RobinhoodTrader  # noqa: E402

logs.clear()
t = RobinhoodTrader()
out("rh-init-auth-nonfatal", authenticated=t._authenticated,
    challenge=t.in_device_challenge_mode, log=[l for l in logs if "[rh]" in l or "box-session" in l][:3])
try:
    t.positions()
    out("rh-ensure-auth-raises", result="NO RAISE")
except RuntimeError as e:
    out("rh-ensure-auth-raises", raised="RuntimeError", msg=str(e))

# now give it a token via the sqlite cache (no box call)
box_session._write_meta(Config.STOP_DB_PATH, "rh_box_token",
                        json.dumps({"token": "tok", "token_type": "Bearer",
                                    "expires_at": time.time() + 3600}))
calls = []
rh.orders.order = lambda *a, **k: (calls.append((a, k)), {"id": "o1", "state": "queued"})[1]
t2 = RobinhoodTrader()
r = t2.submit_order({"symbol": "SPY", "side": "buy", "quantity": 1,
                     "order_type": "limit", "limit_price": None})
out("rh-submit-gtc-swallow:limit-no-price", result=r,
    sent_kwargs=calls[-1][1], has_limitPrice="limitPrice" in calls[-1][1])


def boom(*a, **k):
    raise RuntimeError("rh down")


rh.orders.order = boom
out("rh-submit-gtc-swallow:exception", result=t2.submit_order(
    {"symbol": "SPY", "side": "buy", "quantity": 1}))

# open_orders with an order missing 'cancel'
rh.orders.get_all_stock_orders = lambda: [
    {"id": "a", "state": "confirmed", "instrument": "u1", "side": "buy", "quantity": "1"},
    {"id": "b", "state": "filled", "instrument": "u1", "side": "buy", "quantity": "1"}]
rh.stocks.get_instrument_by_url = lambda u: {"symbol": "SPY"}
out("rh-open-orders-workaround", result=[o["id"] for o in t2.open_orders()])

# option mark fallback
rh.options.get_open_option_positions = lambda: [
    {"quantity": "52", "chain_symbol": "XYZ", "type": "long", "average_price": "177",
     "option": "u", "option_id": "x", "trade_value_multiplier": "100"}]
rh.options.get_option_instrument_data_by_id = lambda i: {
    "strike_price": "230", "expiration_date": "2026-11-20", "type": "call"}
rh.options.get_option_market_data = boom
rh.stocks.get_latest_price = lambda s: ["225"]
p = t2.options_positions()[0]
out("rh-option-entry-cents+mark-fallback", purchase_price=p["purchase_price"],
    mark_price=p["mark_price"], unrealized_pl=p["unrealized_pl"],
    option_type=p["option_type"], position_type=p["position_type"], greeks=p["greeks"])

# cancel: no dry-run, direct to RH
cc = []
rh.orders.cancel_stock_order = lambda i: cc.append(i)
t2.cancel_order("zzz")
out("rh-cancel-not-idempotent", called_rh_with=cc)

# /api/trade: body dry_run:false overrides DRY_RUN=true
from app import create_app  # noqa: E402

app = create_app() if callable(create_app) else None
sub = []
import app.brokers as brokers  # noqa: E402


class FakeBroker:
    def submit_order(self, o):
        sub.append(o)
        return {"id": "o9", "symbol": o["symbol"], "status": "queued"}


brokers._broker_cache["robinhood"] = FakeBroker()
import app.api.trade as trade_mod  # noqa: E402
trade_mod.get_broker = lambda n: brokers._broker_cache[n]
app.config["DRY_RUN"] = True
cli = app.test_client()
routes = [r.rule for r in app.url_map.iter_rules() if "trade" in r.rule]
out("rh-submit-bypasses-box-guardrails:routes", routes=routes)
for body in ({"symbol": "SPY", "side": "buy", "quantity": 1, "broker": "robinhood"},
             {"symbol": "SPY", "side": "buy", "quantity": 1, "broker": "robinhood", "dry_run": False}):
    for rule in routes[:1]:
        resp = cli.post(rule, json=body)
        out("rh-submit-bypasses-box-guardrails", service_DRY_RUN=True,
            body_dry_run=body.get("dry_run"), status=resp.status_code,
            json=resp.get_json(), broker_called=len(sub))

# ---------------- IBKR ----------------
fake = types.ModuleType("ib_async")


class _O:
    def __init__(self, kind, action, qty, *px):
        self.orderType, self.action, self.totalQuantity, self.px = kind, action, qty, px
        self.orderId = 7
        self.tif = None


fake.MarketOrder = lambda a, q: _O("MKT", a, q)
fake.LimitOrder = lambda a, q, p: _O("LMT", a, q, p)
fake.StopOrder = lambda a, q, p: _O("STP", a, q, p)
fake.StopLimitOrder = lambda a, q, l, s: _O("STP LMT", a, q, l, s)
fake.Stock = lambda *a: ("STK",) + a
fake.Option = lambda *a: ("OPT",) + a
placed = []


class IB:
    def isConnected(self):
        return getattr(self, "c", False)

    def connect(self, h, p, clientId):
        if p == 1:
            raise OSError("refused")
        self.c = True

    def managedAccounts(self):
        return ["U1234567"]

    def qualifyContracts(self, c):
        pass

    def placeOrder(self, c, o):
        placed.append((c, o))
        return types.SimpleNamespace(order=o, orderStatus=types.SimpleNamespace(status="Submitted"))

    def reqAllOpenOrders(self):
        pass

    def openTrades(self):
        return []

    def reqGlobalCancel(self):
        placed.append("GLOBAL_CANCEL")


fake.IB = IB
sys.modules["ib_async"] = fake
from app.brokers.ibkr_client import IBKRTrader  # noqa: E402
from app.enums import OrderSide  # noqa: E402

try:
    IBKRTrader("127.0.0.1", 1, 1).account()
except ConnectionError as e:
    out("ibkr-connect-error", raised="ConnectionError", msg=str(e)[:120])

logs.clear()
ib = IBKRTrader("127.0.0.1", 4002, 1, paper=True)
r = ib.submit_order({"symbol": "SPY", "side": OrderSide.BUY, "quantity": 1,
                     "order_type": "stop", "stop_price": None})
out("ibkr-submit-fallback-market", result=r, sent_type=placed[-1][1].orderType, tif=placed[-1][1].tif)
out("ibkr-paper-flag-advisory", paper_flag=True, connect_log=[l for l in logs if "IBKR connected" in l])
try:
    ib._contract({"asset_class": "option", "symbol": "SPY", "expiration": "2026-11-20",
                  "strike": 230, "option_type": "cat"})
except ValueError as e:
    out("ibkr-option-contracts", raised="ValueError", msg=str(e))
out("ibkr-option-contracts:ok", contract=ib._contract(
    {"asset_class": "option", "symbol": "SPY", "expiration": "2026-11-20",
     "strike": 230, "option_type": "call"}))
logs.clear()
out("ibkr-cancel-missing-warns", result=ib.cancel_order("nope"), log=logs[-1:])
ib.cancel_all()
out("ibkr-cancel-all-global", sent=placed[-1])
httpd.shutdown()
