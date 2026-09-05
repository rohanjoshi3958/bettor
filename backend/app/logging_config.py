"""Structured application logging for the Bettor backend.

Configuration (environment variables):

* ``LOG_LEVEL`` — standard level name (default ``INFO``).
* ``LOG_FORMAT`` — ``json`` or ``text``. When unset: ``json`` on Render /
  production hosts, ``text`` for local development.

Log records are JSON objects (one per line) in ``json`` mode so hosting log
drains can parse them. Extra fields passed via ``logger.info("msg", extra={...})``
or the ``log_extra`` helper appear as top-level JSON keys. Request-scoped
``request_id`` is injected automatically when middleware has set it.

API keys and other secrets must never be logged. Prefer structured fields
(``sport_key``, ``status_code``, …) over raw URLs that may embed ``apiKey``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|authorization|passwd|password|secret|token)",
    re.IGNORECASE,
)
_CONFIGURED = False


def get_log_level() -> str:
    """Return the configured log level name (e.g. ``INFO``)."""
    raw = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return raw or "INFO"


def get_log_format() -> str:
    """Return ``json`` or ``text`` based on env / hosting heuristics."""
    raw = os.environ.get("LOG_FORMAT", "").strip().lower()
    if raw in ("json", "text"):
        return raw
    # Render sets RENDER=true; treat explicit production env the same.
    if os.environ.get("RENDER", "").strip().lower() in ("1", "true", "yes"):
        return "json"
    env = os.environ.get("ENVIRONMENT", os.environ.get("ENV", "")).strip().lower()
    if env in ("prod", "production"):
        return "json"
    return "text"


def _redact_value(key: str, value: Any) -> Any:
    if _SECRET_KEY_RE.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, v) for v in value]
    return value


def sanitize_log_extra(extra: dict[str, Any]) -> dict[str, Any]:
    """Copy *extra* with secret-looking keys redacted."""
    return {k: _redact_value(str(k), v) for k, v in extra.items()}


class _ContextFilter(logging.Filter):
    """Attach request_id from the context var when present."""

    def filter(self, record: logging.LogRecord) -> bool:
        rid = request_id_var.get()
        if rid and not getattr(record, "request_id", None):
            record.request_id = rid  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line."""

    _RESERVED = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(sanitize_log_extra(payload), default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Human-readable lines with structured extras as key=value pairs."""

    _SKIP = JsonFormatter._RESERVED | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, self.datefmt)} {record.levelname} [{record.name}] {record.getMessage()}"
        extras: list[str] = []
        for key, value in record.__dict__.items():
            if key in self._SKIP or key.startswith("_"):
                continue
            safe = _redact_value(key, value)
            extras.append(f"{key}={safe!r}")
        if extras:
            base = f"{base} {' '.join(extras)}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging(*, force: bool = False) -> None:
    """Configure the root logger once for the process.

    Safe to call multiple times; subsequent calls are no-ops unless ``force``.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    level_name = get_log_level()
    level = getattr(logging, level_name, logging.INFO)
    fmt_name = get_log_format()
    formatter: logging.Formatter = JsonFormatter() if fmt_name == "json" else TextFormatter(
        datefmt="%Y-%m-%dT%H:%M:%S"
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.addFilter(_ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Keep noisy third-party loggers quieter than the app default.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (call after ``configure_logging`` in production)."""
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """Log *message* with structured fields (secret keys redacted)."""
    logger.log(level, message, extra=sanitize_log_extra(fields))
