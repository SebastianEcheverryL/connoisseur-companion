"""Structured logging with structlog: JSON in production, readable console output in development."""
import logging
import sys

import structlog

from connoisseur.settings import get_settings

_configured = False


def configure_logging() -> None:
    global _configured
    if _configured:
        return
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)

    # Logs go to stderr: stdout is reserved for the MCP stdio transport
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level)
    renderer = structlog.processors.JSONRenderer() if s.log_json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None):
    configure_logging()
    return structlog.get_logger(name)
