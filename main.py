#!/usr/bin/env python3
"""Allocation Engine 2.0 — legacy CLI (run/once/status for AllocationEngine).

Not implemented: the reconciliation engine was removed. Production runs
gunicorn app.wsgi:application; the Render worker starts the sweep loop from
gunicorn.conf.py.
"""

MAX_BACKOFF_SECONDS = 300


def run_once(engine):
    raise NotImplementedError("main.py CLI is not implemented")


def tick_and_report(engine, state: dict, interval: int, heartbeat_path: str = ""):
    raise NotImplementedError("main.py CLI is not implemented")


def run_loop(engine, interval: int):
    raise NotImplementedError("main.py CLI is not implemented")


def status(engine, broker_name: str):
    raise NotImplementedError("main.py CLI is not implemented")


def serve():
    raise NotImplementedError("main.py CLI is not implemented")


def main():
    raise NotImplementedError("main.py CLI is not implemented")


if __name__ == "__main__":
    main()
