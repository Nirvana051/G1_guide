"""Structured logging with request correlation and credential redaction."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Tuple

__all__ = [
    "setup_logging",
    "get_logger",
    "context_scope",
    "redact",
    "DEFAULT_REDACT_KEYS",
    "CONTEXT_FIELDS",
]

DEFAULT_REDACT_KEYS: Tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "cookie",
    "private_key",
)

CONTEXT_FIELDS: Tuple[str, ...] = (
    "request_id",
    "command_id",
    "action_id",
    "client",
    "duration_ms",
)

REDACTED = "***"

_context: ContextVar[Mapping[str, Any]] = ContextVar("g1_api_log_context", default={})

_STANDARD_RECORD_ATTRS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "taskName", "thread", "threadName",
    }
)


def current_context() -> Dict[str, Any]:
    return dict(_context.get())


def bind_context(**values: Any) -> Any:
    merged = dict(_context.get())
    for key, value in values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return _context.set(merged)


def unbind_context(token: Any) -> None:
    _context.reset(token)


@contextlib.contextmanager
def context_scope(**values: Any) -> Iterator[Dict[str, Any]]:
    token = bind_context(**values)
    try:
        yield current_context()
    finally:
        unbind_context(token)


def _is_secret(key: Any, redact_keys: Sequence[str]) -> bool:
    text = str(key).lower()
    return any(marker in text for marker in redact_keys)


def redact(value: Any, redact_keys: Sequence[str] = DEFAULT_REDACT_KEYS) -> Any:
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            if _is_secret(key, redact_keys):
                out[str(key)] = REDACTED
            else:
                out[str(key)] = redact(item, redact_keys)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [redact(item, redact_keys) for item in value]
    return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        for key, value in _context.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class StructuredFormatter(logging.Formatter):
    def __init__(
        self,
        json_logs: bool = False,
        include_timestamp: bool = True,
        redact_keys: Sequence[str] = DEFAULT_REDACT_KEYS,
    ) -> None:
        super().__init__()
        self.json_logs = bool(json_logs)
        self.include_timestamp = bool(include_timestamp)
        self.redact_keys = tuple(str(k).lower() for k in redact_keys)

    def _collect(self, record: logging.LogRecord) -> Dict[str, Any]:
        custom: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_ATTRS or key.startswith("_"):
                continue
            if key == "fields":
                continue
            custom[key] = value
        nested = getattr(record, "fields", None)
        if isinstance(nested, Mapping):
            custom.update(dict(nested))
        return redact(custom, self.redact_keys)

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        custom = self._collect(record)
        message = record.getMessage()
        ordered: Dict[str, Any] = {}
        if self.include_timestamp:
            ordered["ts"] = (
                time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                + ".%03dZ" % (record.msecs,)
            )
        ordered["level"] = record.levelname
        ordered["logger"] = record.name
        ordered["message"] = message
        for key in CONTEXT_FIELDS:
            if key in custom:
                ordered[key] = custom.pop(key)
        ordered.update(custom)
        if record.exc_info:
            ordered["exception"] = self.formatException(record.exc_info)
        if self.json_logs:
            return json.dumps(ordered, default=str, ensure_ascii=False)
        head_keys = ("ts", "level", "logger", "message")
        prefix = " ".join(
            str(ordered[key]) for key in ("ts", "level", "logger") if key in ordered
        )
        head = "%s | %s" % (prefix, message) if prefix else message
        tail = " ".join(
            "%s=%s" % (key, _plain(value))
            for key, value in ordered.items()
            if key not in head_keys and key not in ("exception", "stack")
        )
        line = head if not tail else "%s | %s" % (head, tail)
        if "exception" in ordered:
            line = "%s\n%s" % (line, ordered["exception"])
        return line


def _plain(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str, ensure_ascii=False)
    text = str(value)
    return '"%s"' % text if " " in text else text


def setup_logging(cfg: Optional[Any] = None, stream: Any = None) -> logging.Logger:
    section = getattr(cfg, "logging", cfg)
    level_name = str(getattr(section, "level", "INFO")).upper()
    level = logging.getLevelName(level_name)
    if not isinstance(level, int):
        level = logging.INFO
    json_logs = bool(getattr(section, "json_logs", False))
    include_timestamp = bool(getattr(section, "include_timestamp", True))
    redact_keys = tuple(getattr(section, "redact_keys", DEFAULT_REDACT_KEYS))

    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(
        StructuredFormatter(
            json_logs=json_logs,
            include_timestamp=include_timestamp,
            redact_keys=redact_keys,
        )
    )
    handler.addFilter(ContextFilter())
    handler.set_name("g1_api")

    logger = logging.getLogger("g1_api")
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        with contextlib.suppress(Exception):
            existing.close()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    if not name:
        return logging.getLogger("g1_api")
    if name == "g1_api" or name.startswith("g1_api."):
        return logging.getLogger(name)
    return logging.getLogger("g1_api.%s" % (name,))
