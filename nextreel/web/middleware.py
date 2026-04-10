import re
from uuid import uuid4
from quart import request, g
from logging_config import get_logger

# Only allow safe characters in correlation IDs to prevent log injection
_SAFE_CORRELATION_RE = re.compile(r"^[\w\-\.]{1,128}$")

# Path prefixes for which we still assign a correlation ID but suppress
# the per-request "New request received" log line. Kept in sync with the
# request-handler skip list in app.py (which imports this tuple).
_CORRELATION_LOG_SKIP_PREFIXES = (
    "/static",
    "/favicon.ico",
    "/health",
    "/ready",
    "/metrics",
)

logger = get_logger(__name__)


async def add_correlation_id():
    """
    Middleware to add a correlation ID to every request.
    """
    raw_id = request.headers.get("X-Correlation-ID", "")
    if raw_id and _SAFE_CORRELATION_RE.match(raw_id):
        correlation_id = raw_id
    else:
        correlation_id = str(uuid4())
    g.correlation_id = correlation_id
    if not request.path.startswith(_CORRELATION_LOG_SKIP_PREFIXES):
        logger.info(
            "New request received. Correlation ID: %s, Path: %s",
            correlation_id,
            request.path,
        )
