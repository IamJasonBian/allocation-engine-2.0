"""Reauth Claude Code from inside the box.

POST /api/claude/reauth
    Start the Claude login flow (CLAUDE_LOGIN_CMD), capture the browser
    callback URL it prints, and return it so it can be opened. Optionally
    ``wait`` for verification to complete.
GET  /api/claude/reauth/<session_id>
    Poll a pending login: reports success once verification completes.

Single gunicorn worker (4 threads) → the in-process session registry is shared.
Requires the Claude CLI to be installed in the box; verify after first deploy.
"""

import logging
import os
import re
import shlex
import subprocess
import threading
import uuid

from flask import Blueprint, jsonify, request, current_app

log = logging.getLogger(__name__)

bp = Blueprint("claude_auth", __name__)

_URL_RE = re.compile(r"https?://\S+")
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def _reader(session_id, proc):
    """Drain the login process output; capture the first URL it prints."""
    for raw in iter(proc.stdout.readline, ""):
        line = raw.rstrip("\n")
        with _lock:
            sess = _sessions.get(session_id)
            if sess is None:
                break
            sess["output"].append(line)
            if sess["auth_url"] is None:
                match = _URL_RE.search(line)
                if match:
                    sess["auth_url"] = match.group(0)
    proc.stdout.close()
    proc.wait()
    with _lock:
        sess = _sessions.get(session_id)
        if sess is not None:
            sess["returncode"] = proc.returncode


def _verified() -> bool:
    """True once the Claude credentials file exists (verification complete)."""
    return os.path.exists(current_app.config.get("CLAUDE_CREDENTIALS_PATH", ""))


def _status(session_id, sess) -> dict:
    rc = sess.get("returncode")
    if rc is None:
        state = "pending"
    elif rc == 0 and _verified():
        state = "success"
    else:
        state = "failed"
    return {
        "session_id": session_id,
        "status": state,
        "auth_url": sess.get("auth_url"),
        "returncode": rc,
        "verified": _verified(),
    }


@bp.route("/claude/reauth", methods=["POST"])
def reauth():
    cmd = current_app.config.get("CLAUDE_LOGIN_CMD", "")
    if not cmd:
        return jsonify({"error": "CLAUDE_LOGIN_CMD is not set"}), 503

    try:
        proc = subprocess.Popen(
            shlex.split(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except (OSError, ValueError) as e:
        log.error("claude reauth failed to start '%s': %s", cmd, e)
        return jsonify({"error": "failed to start login", "detail": str(e)}), 500

    session_id = uuid.uuid4().hex
    with _lock:
        _sessions[session_id] = {"auth_url": None, "output": [], "returncode": None,
                                 "proc": proc}
    threading.Thread(target=_reader, args=(session_id, proc), daemon=True).start()

    # Give the login flow a moment to emit its callback URL.
    body = request.get_json(silent=True) or {}
    wait = body.get("wait", request.args.get("wait"))
    deadline = float(wait) if wait not in (None, "", True) else (30.0 if wait else 5.0)

    try:
        proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        pass  # still running — return the URL for the user to approve

    with _lock:
        sess = _sessions[session_id]
        result = _status(session_id, sess)
    if result["status"] == "pending" and result["auth_url"] is None:
        log.warning("claude reauth: no callback URL captured within %ss", deadline)
    return jsonify(result)


@bp.route("/claude/reauth/<session_id>", methods=["GET"])
def reauth_status(session_id):
    with _lock:
        sess = _sessions.get(session_id)
        if sess is None:
            return jsonify({"error": "unknown session_id"}), 404
        return jsonify(_status(session_id, sess))
