import re
from uuid import uuid4
from quart import request, g
from logging_config import get_logger

# Only allow safe characters in correlation IDs to prevent log injection
_SAFE_CORRELATION_RE = re.compile(r"^[\w\-\.]{1,128}$")

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
    logger.info("New request received. Correlation ID: %s, Path: %s", correlation_id, request.path)
