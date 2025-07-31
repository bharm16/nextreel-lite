import logging
from logging.handlers import RotatingFileHandler


def setup_logging(log_level=logging.INFO):
    """
    Configures logging for the application with both console and rotating file handlers.
    """
    log_format = (
        "%(asctime)s [%(levelname)s] %(name)s - %(funcName)s: %(message)s"
    )

    # Console handler for on-screen logging
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))

    # File handler with rotation (max 5MB per file, keep 3 backups)
    file_handler = RotatingFileHandler(
        "app.log", maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setFormatter(logging.Formatter(log_format))

    # Configure the root logger
    logging.basicConfig(
        level=log_level,
        handlers=[console_handler, file_handler]
    )

    # Log the initialization of logging
    logging.getLogger(__name__).info(
        "Logging initialized with level: %s", logging.getLevelName(log_level)
    )

