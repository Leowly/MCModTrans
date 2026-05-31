"""Structured logging configuration."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
DATE_FORMAT = "%H:%M:%S"


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> logging.Logger:
    """Configure the modtrans package logger.

    Args:
        level: Log level name (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional file path for persistent log output.

    Returns:
        Configured root modtrans logger.
    """
    logger = logging.getLogger("modtrans")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers (idempotent)
    logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(file_handler)

    return logger
