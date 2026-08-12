"""Heartbeat file for the unattended (remote) engine loop.

The IBKR gateway box runs ``main.py --broker ibkr run`` with no HTTP server, so
health is surfaced as an atomically-written JSON file that a sidecar, an
operator (``docker exec`` / a mounted volume), or a health check can read. It
captures liveness, the last successful tick, and the current failure streak so
a stuck gateway (Sunday maintenance window, weekly re-auth) is visible rather
than silent.
"""

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

DEFAULT_PATH = os.getenv("ENGINE_HEARTBEAT_PATH",
                         os.path.join(os.path.dirname(__file__), "..", "data",
                                      "heartbeat.json"))


def write_heartbeat(path, *, status, tick_count, consecutive_errors,
                    last_error=None, account=None):
    """Atomically write the engine heartbeat as JSON.

    Written every loop iteration (ok on a clean tick, error on a failure) so the
    file's freshness is liveness and its fields are health. The write is atomic
    (temp file + ``os.replace``) so a reader never sees a half-written file.

    Args:
        path: destination file path.
        status: ``"ok"`` for a clean tick, ``"error"`` for a failed one.
        tick_count: cumulative successful ticks since start.
        consecutive_errors: current failure streak (0 when healthy).
        last_error: message of the most recent failure, or None.
        account: broker account id last seen, if known.

    Returns:
        The heartbeat payload dict that was written.
    """
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "tick_count": tick_count,
        "consecutive_errors": consecutive_errors,
        "last_error": last_error,
        "account": account,
    }
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)   # atomic on POSIX
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return payload
