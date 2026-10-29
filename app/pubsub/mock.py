"""Localhost mock pub/sub. No GCP. Bind loopback only.

    python -m app.pubsub.mock
    python -m app.pubsub.mock --port 5055
"""

from __future__ import annotations

import argparse

from flask import Flask, jsonify, request

from app.pubsub.bus import MockBus
from app.pubsub.envelope import EncodeError
from app.pubsub.reducer import Reducer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5055


def create_mock_app(bus=None):
    app = Flask("pubsub-mock")
    app.bus = bus or MockBus()
    app.reducer = Reducer(app.bus)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "service": "pubsub-mock",
                        "subscriptions": sorted(app.bus.subscriptions())})

    @app.post("/subscribe")
    def subscribe():
        body = request.get_json(silent=True) or {}
        topic, name = body.get("topic"), body.get("name")
        if not topic or not name:
            return jsonify({"error": "topic and name are required"}), 400
        app.bus.subscribe(topic, name)
        return jsonify({"topic": topic, "name": name})

    @app.post("/publish")
    def publish():
        body = request.get_json(silent=True) or {}
        try:
            published = app.bus.publish(
                body.get("topic"),
                body.get("event") or {},
                event_id=body.get("event_id"),
                ordering_key=body.get("ordering_key"),
                source=body.get("source", "rh-poll"),
            )
        except EncodeError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"id": published.id, "message_id": published.message_id})

    @app.get("/pull/<name>")
    def pull(name):
        if name not in app.bus.subscriptions():
            return jsonify({"error": "unknown subscription", "name": name}), 404
        msgs = app.bus.pull(name)
        return jsonify({"count": len(msgs), "messages": msgs})

    @app.post("/seed")
    def seed():
        body = request.get_json(silent=True) or {}
        key, q = body.get("ordering_key"), body.get("q")
        if not key or q is None:
            return jsonify({"error": "ordering_key and q are required"}), 400
        app.reducer.seed(key, q)
        return jsonify({"ordering_key": key, "q": float(q)})

    @app.post("/handle")
    def handle():
        body = request.get_json(silent=True) or {}
        if not body.get("topic") or not body.get("event_id"):
            return jsonify({"error": "topic and event_id are required"}), 400
        try:
            app.reducer.handle(body)
        except EncodeError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": True, "event_id": body["event_id"]})

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Localhost mock pub/sub")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("mock bus binds loopback only")
    app = create_mock_app()
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
