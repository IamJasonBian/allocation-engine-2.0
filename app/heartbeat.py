"""Heartbeat file for the unattended IBKR engine loop (main.py).

Not implemented: the IBKR loop it reported on is gone.
"""

import os

DEFAULT_PATH = os.getenv("ENGINE_HEARTBEAT_PATH",
                         os.path.join(os.path.dirname(__file__), "..", "data",
                                      "heartbeat.json"))


def write_heartbeat(path, *, status, tick_count, consecutive_errors,
                    last_error=None, account=None):
    raise NotImplementedError("write_heartbeat is not implemented")
