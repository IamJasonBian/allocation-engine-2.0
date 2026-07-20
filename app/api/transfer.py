"""Funding API — deposit/withdraw between a linked bank account and a broker,
plus a convenience endpoint to move funds between our two broker accounts.

There is no direct broker-to-broker rail: moving money "from Robinhood to
IBKR" is two independent ACH legs against the same linked bank account
(withdraw from one broker, deposit into the other), not a single transfer
call. IBKR's Client Portal Web API does not expose fund-transfer initiation
at all for retail accounts (see IBKRTrader), so IBKR-side deposit/withdraw/
history calls return 501 until that changes — Robinhood's side is fully
functional via robin_stocks' ACH endpoints.
"""

from flask import Blueprint, jsonify, request, current_app
from app.brokers import get_broker

bp = Blueprint("transfer", __name__)


def _unsupported(broker_name, e):
    return jsonify({"error": str(e), "broker": broker_name, "supported": False}), 501


def _dry_run(body: dict) -> bool:
    return bool(body.get("dry_run", body.get("dryRun", current_app.config.get("DRY_RUN", True))))


@bp.route("/transfer/bank-accounts/<broker_name>")
def bank_accounts(broker_name):
    try:
        broker = get_broker(broker_name)
        return jsonify({"broker": broker_name, "accounts": broker.linked_bank_accounts()})
    except NotImplementedError as e:
        return _unsupported(broker_name, e)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer/deposit/<broker_name>", methods=["POST"])
def deposit(broker_name):
    """Deposit funds from a linked bank account into `broker_name`.

    Body JSON:
        amount:           float (required) dollar amount, > 0
        ach_relationship: str   (required for Robinhood) linked bank account id
        dry_run:          bool  (optional) override global dry_run setting
    """
    body = request.get_json(silent=True) or {}
    amount = body.get("amount")
    if amount is None:
        return jsonify({"error": "amount is required"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    if _dry_run(body):
        return jsonify({
            "status": "simulated", "dry_run": True, "broker": broker_name, "amount": amount,
            "message": "Deposit validated but not submitted (dry_run=true)",
        })

    try:
        broker = get_broker(broker_name)
        result = broker.deposit(amount, ach_relationship=body.get("ach_relationship"))
        if result is None:
            return jsonify({"error": "Broker rejected the deposit"}), 502
        return jsonify({"status": "submitted", "broker": broker_name, **result}), 201
    except NotImplementedError as e:
        return _unsupported(broker_name, e)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer/withdraw/<broker_name>", methods=["POST"])
def withdraw(broker_name):
    """Withdraw funds from `broker_name` to a linked bank account.

    Body JSON:
        amount:           float (required) dollar amount, > 0
        ach_relationship: str   (required for Robinhood) linked bank account id
        dry_run:          bool  (optional) override global dry_run setting
    """
    body = request.get_json(silent=True) or {}
    amount = body.get("amount")
    if amount is None:
        return jsonify({"error": "amount is required"}), 400
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({"error": "amount must be a number"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    if _dry_run(body):
        return jsonify({
            "status": "simulated", "dry_run": True, "broker": broker_name, "amount": amount,
            "message": "Withdrawal validated but not submitted (dry_run=true)",
        })

    try:
        broker = get_broker(broker_name)
        result = broker.withdraw(amount, ach_relationship=body.get("ach_relationship"))
        if result is None:
            return jsonify({"error": "Broker rejected the withdrawal"}), 502
        return jsonify({"status": "submitted", "broker": broker_name, **result}), 201
    except NotImplementedError as e:
        return _unsupported(broker_name, e)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer/history/<broker_name>")
def history(broker_name):
    try:
        broker = get_broker(broker_name)
        return jsonify({
            "broker": broker_name,
            "transfers": broker.transfer_history(direction=request.args.get("direction")),
        })
    except NotImplementedError as e:
        return _unsupported(broker_name, e)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/transfer/between", methods=["POST"])
def between():
    """Move funds between our two broker accounts via their shared bank
    account: withdraw `amount` from `from_broker`, then deposit it into
    `to_broker`. The two legs are independent ACH calls — this does not wait
    for the withdrawal to settle before firing the deposit, so keep amounts
    within what the source account can safely float.

    Body JSON:
        from_broker: str   (required) "robinhood" or "ibkr"
        to_broker:   str   (required) "robinhood" or "ibkr", must differ from from_broker
        amount:      float (required) dollar amount, > 0
        from_ach_relationship: str (required for Robinhood as source)
        to_ach_relationship:   str (required for Robinhood as destination)
        dry_run:     bool  (optional) override global dry_run setting
    """
    body = request.get_json(silent=True) or {}
    from_broker = body.get("from_broker")
    to_broker = body.get("to_broker")
    amount = body.get("amount")

    errors = []
    if from_broker not in ("robinhood", "ibkr"):
        errors.append("from_broker must be 'robinhood' or 'ibkr'")
    if to_broker not in ("robinhood", "ibkr"):
        errors.append("to_broker must be 'robinhood' or 'ibkr'")
    if from_broker == to_broker and not errors:
        errors.append("from_broker and to_broker must differ")
    if amount is None:
        errors.append("amount is required")
    else:
        try:
            amount = float(amount)
            if amount <= 0:
                errors.append("amount must be positive")
        except (TypeError, ValueError):
            errors.append("amount must be a number")
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400

    if _dry_run(body):
        return jsonify({
            "status": "simulated", "dry_run": True,
            "from_broker": from_broker, "to_broker": to_broker, "amount": amount,
            "message": "Transfer validated but not submitted (dry_run=true)",
        })

    try:
        source = get_broker(from_broker)
        withdrawal = source.withdraw(amount, ach_relationship=body.get("from_ach_relationship"))
        if withdrawal is None:
            return jsonify({"error": f"{from_broker} rejected the withdrawal"}), 502
    except NotImplementedError as e:
        return _unsupported(from_broker, e)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    try:
        dest = get_broker(to_broker)
        deposit_result = dest.deposit(amount, ach_relationship=body.get("to_ach_relationship"))
    except Exception as e:
        return jsonify({
            "status": "partial",
            "withdrawal": withdrawal,
            "deposit_error": str(e),
            "message": f"Withdrew from {from_broker} but the {to_broker} deposit "
                       "failed or is unsupported — complete it manually.",
        }), 207

    if deposit_result is None:
        return jsonify({
            "status": "partial", "withdrawal": withdrawal,
            "message": f"Withdrew from {from_broker} but {to_broker} rejected the deposit",
        }), 207

    return jsonify({
        "status": "submitted", "from_broker": from_broker, "to_broker": to_broker,
        "amount": amount, "withdrawal": withdrawal, "deposit": deposit_result,
    }), 201
