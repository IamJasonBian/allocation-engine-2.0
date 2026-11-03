"""Worker-lifetime log_task_id.

One id per process. Minted when the core loop starts (run_engine_loop),
kept until that gunicorn worker exits.
"""

import logging
import uuid

_TASK_ID: str | None = None


def reset_task_id() -> None:
    """Clear the bound id (tests / a fresh worker)."""
    global _TASK_ID
    _TASK_ID = None


def get_task_id() -> str | None:
    return _TASK_ID


def ensure_task_id() -> str:
    """Return the worker id, minting one if this process does not have it yet."""
    global _TASK_ID
    if _TASK_ID is None:
        _TASK_ID = uuid.uuid4().hex
    return _TASK_ID


class TaskIdFilter(logging.Filter):
    """Stamp every log record with log_task_id (or '-' before the loop starts)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.log_task_id = _TASK_ID or "-"
        return True


def install_task_id_logging() -> None:
    """Attach the filter so the log format can render %(log_task_id)s."""
    root = logging.getLogger()
    if not any(isinstance(f, TaskIdFilter) for f in root.filters):
        root.addFilter(TaskIdFilter())
    for handler in root.handlers:
        if not any(isinstance(f, TaskIdFilter) for f in handler.filters):
            handler.addFilter(TaskIdFilter())
