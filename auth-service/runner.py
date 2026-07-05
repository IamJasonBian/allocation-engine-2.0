"""Run external commands and capture their results.

Commands run as the service user, in the authenticated context of the process
(the login session is already established when EXEC_REQUIRE_AUTH is on). Output
is captured and returned structurally so the caller can inspect it.
"""

import logging
import shlex
import subprocess

import config

log = logging.getLogger("runner")


def run_command(command, cwd: str | None = None) -> dict:
    """Execute `command` and return {exit_code, stdout, stderr, timed_out}.

    `command` may be a string (parsed with shlex) or an argv list. A list is
    passed straight to subprocess (no shell), which is the safer form.
    """
    if isinstance(command, str):
        argv = shlex.split(command)
    elif isinstance(command, list):
        argv = command
    else:
        raise ValueError("command must be a string or a list of strings")

    if not argv:
        raise ValueError("command is empty")

    log.info("exec: %s", argv)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=config.EXEC_TIMEOUT_SECONDS,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as e:
        return {
            "exit_code": None,
            "stdout": e.stdout or "",
            "stderr": (e.stderr or "") + "\n[timed out]",
            "timed_out": True,
        }
