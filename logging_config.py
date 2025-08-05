"""Centralised logging configuration for the application.

Only two helper functions are exposed: :func:`setup_logging` which configures a
root logger with both console and rotating file handlers, and :func:`get_logger`
which is a thin wrapper around :func:`logging.getLogger` for convenience.
"""

import logging
from logging.handlers import RotatingFileHandler


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configure application logging.

    Parameters
    ----------
    log_level : int
        Threshold for the root logger (e.g. ``logging.INFO``).
    """

    log_format = "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s: %(message)s"

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))

    file_handler = RotatingFileHandler(
        "app.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(log_format))

    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])

    # Reduce noise from verbose third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialized with level: %s", logging.getLevelName(log_level)
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module specific logger."""
    return logging.getLogger(name)

