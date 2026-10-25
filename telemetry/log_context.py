"""Log correlation: every line written while a notification is handled carries its tenant, notification and attempt ids."""
from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from config.logging import LoggingSettings

FIELDS = ("tenant_id", "notification_id", "attempt_id")

_ctx: ContextVar[dict[str, str]] = ContextVar("pulse_log_context", default={})


@contextmanager
def bind(**fields: Any) -> Iterator[None]:
    """Add ids to every log line inside the block (nested binds merge; None values are ignored)."""
    token = _ctx.set({**_ctx.get(), **{k: str(v) for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


def update_current(**fields: Any) -> None:
    """Add ids discovered mid-way (e.g. the notification id once loaded). Undone when the enclosing `bind` exits."""
    _ctx.set({**_ctx.get(), **{k: str(v) for k, v in fields.items() if v is not None}})


class CorrelationFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        current = _ctx.get()
        for name in FIELDS:
            setattr(record, name, current.get(name, "-"))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in FIELDS:
            value = getattr(record, name, "-")
            if value != "-":
                data[name] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


_TEXT = "%(asctime)s %(levelname)s %(name)s [tenant=%(tenant_id)s notification=%(notification_id)s attempt=%(attempt_id)s] %(message)s"


def configure_logging(settings: LoggingSettings) -> None:
    """Root handler with correlation fields; JSON or text per `logging.json`. Replaces earlier basicConfig calls."""
    handler = logging.StreamHandler()
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(JsonFormatter() if settings.json_format else logging.Formatter(_TEXT))
    root = logging.getLogger()
    for old in list(root.handlers):
        if getattr(old, "_pulse", False):
            root.removeHandler(old)
    handler._pulse = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(settings.level)
