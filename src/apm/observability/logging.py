"""Structured logging with secret redaction (spec §58, §63: never log secrets)."""

from __future__ import annotations

import logging
import re
from typing import Any

import structlog

# Keys whose values must always be scrubbed if they ever reach a log event.
_SENSITIVE_KEYS = {
    "api_key",
    "anthropic_api_key",
    "app_key",
    "app_secret",
    "webull_app_key",
    "webull_app_secret",
    "secret",
    "password",
    "token",
    "authorization",
    "access_token",
}

# Coarse pattern for anything that looks like a bearer/api token in free text.
_TOKEN_RE = re.compile(r"(sk-[A-Za-z0-9\-_]{8,}|Bearer\s+[A-Za-z0-9\.\-_]+)")


def _redact_processor(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***redacted***"
    msg = event_dict.get("event")
    if isinstance(msg, str):
        event_dict["event"] = _TOKEN_RE.sub("***redacted***", msg)
    return event_dict


def configure_logging(*, level: str = "INFO", json: bool = True) -> None:
    """Configure structlog + stdlib logging once, at process start."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
    )

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
