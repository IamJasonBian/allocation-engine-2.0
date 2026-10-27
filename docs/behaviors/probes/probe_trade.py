import json, sys
sys.path.insert(0, sys.argv[1])
from app import create_app
import app.brokers as brokers, app.api.trade as trade_mod
sub = []
class FakeBroker:
    def submit_order(self, o):
        sub.append(o); return {"id": "o9", "symbol": o["symbol"], "status": "queued"}
trade_mod.get_broker = lambda n: FakeBroker()
app = create_app(); app.config["DRY_RUN"] = True
c = app.test_client()
for body in ({"symbol":"SPY","side":"BUY","quantity":1},
             {"symbol":"SPY","side":"BUY","quantity":1,"dry_run":False}):
    r = c.post("/api/trade/order/robinhood", json=body)  # no Authorization header
    print(json.dumps({"probe":"rh-submit-bypasses-box-guardrails","service_DRY_RUN":True,
        "body_dry_run":body.get("dry_run"),"auth_header":None,"status":r.status_code,
        "json":r.get_json(),"broker_submit_calls":len(sub)}))
