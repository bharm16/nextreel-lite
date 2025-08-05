import logging
import re
from logging.handlers import RotatingFileHandler


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configure application logging."""
    log_format = "%(asctime)s [%(levelname)s] %(name)s:%(funcName)s: %(message)s"

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))

    file_handler = RotatingFileHandler("app.log", maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(logging.Formatter(log_format))

    redact_filter = RedactFilter()
    console_handler.addFilter(redact_filter)
    file_handler.addFilter(redact_filter)

    logging.basicConfig(level=log_level, handlers=[console_handler, file_handler])

    # Reduce noise from libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging initialized with level: %s", logging.getLevelName(log_level)
    )


def get_logger(name: str) -> logging.Logger:
    """Return a module specific logger."""
    return logging.getLogger(name)


class RedactFilter(logging.Filter):
    """Simple logging filter to redact common secret patterns."""

    patterns = ["password", "secret", "api_key", "token"]

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for pattern in self.patterns:
            regex = re.compile(rf"(?i){pattern}=([^\s]+)")
            message = regex.sub(f"{pattern}=[REDACTED]", message)
        record.msg = message
        return True

