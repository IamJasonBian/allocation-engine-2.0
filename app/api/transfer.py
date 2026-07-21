"""Transfer API — deposit/withdraw against a broker's own linked bank
account, and orchestrated cross-broker transfers (Robinhood <-> IBKR).

There is no direct broker-to-broker transfer API: a "transfer" here is two
independent ACH legs (withdraw from the source's linked bank, deposit to
the destination's linked bank). It is NOT atomic — either leg can fail
independently, and ACH settlement takes days. Real money only moves when
TRANSFERS_DRY_RUN=false AND the request body sets armed=true.
"""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("transfer", __name__)


def _resolve_dry_run(body: dict) -> bool:
    if "dry_run" in body or "dryRun" in body:
        return bool(body.get("dry_run", body.get("dryRun")))
    return current_app.config.get("TRANSFERS_DRY_RUN", True)


def _validate_amount(body: dict):
    amount = body.get("amount")
    if amount is None:
        return None, "amount is required"
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return None, "amount must be a number"
    if amount <= 0:
        return None, "amount must be positive"
    return amount, None


def _do_leg(broker_name: str, amount: float, action: str) -> dict:
    """action: 'deposit' or 'withdraw'. Returns a result dict; raises on failure."""
    broker = get_broker(broker_name)
    fn = getattr(broker, action, None)
    if fn is None:
        raise LookupError(f"{broker_name} does not support {action}")
    result = fn(amount)
    if result is None:
        raise RuntimeError(f"{broker_name} rejected the {action}")
    return result


@bp.route("/transfer/deposit/<broker_name>", methods=["POST"])
def deposit(broker_name):
    """Deposit from broker_name's own linked bank account into broker_name."""
    body = request.get_json(silent=True) or {}
    amount, err = _validate_amount(body)
    if err:
        return jsonify({"error": err}), 400

    dry_run = _resolve_dry_run(body)
    if dry_run:
        return jsonify({
            "status": "simulated", "dry_run": True, "broker": broker_name,
            "action": "deposit", "amount": amount,
            "message": "Deposit validated but not submitted (dry_run=true)",
        })
    if not body.get("armed", False):
        return jsonify({"error": "armed=true is required to submit a real deposit"}), 400

    try:
        result = _do_leg(broker_name, amount, "deposit")
        return jsonify({"status": "submitted", "broker": broker_name, "action": "deposit",
                        "amount": amount, "result": result}), 201
    except NotImplementedError as e:
        return jsonify({"error": str(e)}), 501
    except LookupError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer/withdraw/<broker_name>", methods=["POST"])
def withdraw(broker_name):
    """Withdraw from broker_name to broker_name's own linked bank account."""
    body = request.get_json(silent=True) or {}
    amount, err = _validate_amount(body)
    if err:
        return jsonify({"error": err}), 400

    dry_run = _resolve_dry_run(body)
    if dry_run:
        return jsonify({
            "status": "simulated", "dry_run": True, "broker": broker_name,
            "action": "withdraw", "amount": amount,
            "message": "Withdrawal validated but not submitted (dry_run=true)",
        })
    if not body.get("armed", False):
        return jsonify({"error": "armed=true is required to submit a real withdrawal"}), 400

    try:
        result = _do_leg(broker_name, amount, "withdraw")
        return jsonify({"status": "submitted", "broker": broker_name, "action": "withdraw",
                        "amount": amount, "result": result}), 201
    except NotImplementedError as e:
        return jsonify({"error": str(e)}), 501
    except LookupError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer", methods=["POST"])
def transfer():
    """Move money between two broker accounts: withdraw from from_broker's
    linked bank, deposit to to_broker's linked bank. Two independent ACH
    legs, not atomic — if the deposit leg fails after a successful
    withdrawal, the response says so explicitly rather than pretending the
    transfer completed or rolled back.
    """
    body = request.get_json(silent=True) or {}
    from_broker = body.get("from_broker")
    to_broker = body.get("to_broker")
    if not from_broker or not to_broker:
        return jsonify({"error": "from_broker and to_broker are required"}), 400
    if from_broker == to_broker:
        return jsonify({"error": "from_broker and to_broker must differ"}), 400

    amount, err = _validate_amount(body)
    if err:
        return jsonify({"error": err}), 400

    dry_run = _resolve_dry_run(body)
    if dry_run:
        return jsonify({
            "status": "simulated", "dry_run": True,
            "from_broker": from_broker, "to_broker": to_broker, "amount": amount,
            "message": "Transfer validated but not submitted (dry_run=true)",
        })
    if not body.get("armed", False):
        return jsonify({"error": "armed=true is required to submit a real transfer"}), 400

    try:
        withdraw_result = _do_leg(from_broker, amount, "withdraw")
    except NotImplementedError as e:
        return jsonify({"error": str(e), "leg": "withdraw"}), 501
    except LookupError as e:
        return jsonify({"error": str(e), "leg": "withdraw"}), 400
    except Exception as e:
        return jsonify({"error": str(e), "leg": "withdraw"}), 500

    try:
        deposit_result = _do_leg(to_broker, amount, "deposit")
    except Exception as e:
        status = 501 if isinstance(e, NotImplementedError) else 502
        return jsonify({
            "error": str(e),
            "leg": "deposit",
            "warning": (
                f"${amount} was already withdrawn from {from_broker} "
                f"(id={withdraw_result.get('id')}) but the deposit into "
                f"{to_broker} failed — this is NOT rolled back automatically, "
                "follow up manually."
            ),
            "withdraw_result": withdraw_result,
        }), status

    return jsonify({
        "status": "submitted",
        "from_broker": from_broker, "to_broker": to_broker, "amount": amount,
        "withdraw_result": withdraw_result,
        "deposit_result": deposit_result,
    }), 201
