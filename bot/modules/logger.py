"""Structured logging setup with rotating file handlers.

Why rotation: the bot runs as a long-lived systemd service so an
un-rotated ``FileHandler`` would grow ``activity.log`` without bound
until the VPS disk fills up. ``RotatingFileHandler`` caps each file at
``LOG_FILE_MAX_BYTES`` and keeps ``LOG_FILE_BACKUP_COUNT`` archives.

The module also fails soft when the logs directory is read-only: it
swaps in a ``NullHandler`` instead of crashing at import time so the
bot still runs (without persistent logs) during misconfigured deploys.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog


DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per file
DEFAULT_BACKUP_COUNT = 5


def _file_handler(path: Path, level: int) -> logging.Handler:
    """Return a rotating file handler, or NullHandler on failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=int(os.getenv("LOG_FILE_MAX_BYTES", str(DEFAULT_MAX_BYTES))),
            backupCount=int(os.getenv("LOG_FILE_BACKUP_COUNT", str(DEFAULT_BACKUP_COUNT))),
            encoding="utf-8",
        )
        handler.setLevel(level)
        return handler
    except OSError as exc:  # read-only fs, permission errors, etc.
        sys_handler = logging.NullHandler()
        sys_handler.setLevel(level)
        logging.getLogger(__name__).warning(
            "Disabling file logging at %s (%s)", path, exc
        )
        return sys_handler


def setup_logging() -> None:
    """Configure structured logging with rotating file + console output."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_dir = Path(os.getenv("LOG_DIR", str(Path(__file__).parent.parent / "logs")))

    file_handler = _file_handler(log_dir / "activity.log", logging.DEBUG)
    error_handler = _file_handler(log_dir / "error.log", logging.ERROR)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, log_level, logging.INFO))

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[file_handler, error_handler, console_handler],
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level, logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
