import logging
from uuid import uuid4
from quart import request, g

logger = logging.getLogger(__name__)

async def add_correlation_id():
    """
    Middleware to add a correlation ID to every request.
    """
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid4()))
    g.correlation_id = correlation_id
    logger.debug(
        "New request received. Correlation ID: %s, Path: %s",
        correlation_id,
        request.path,
    )
