"""Collection of simple middleware utilities used by the Quart app."""

import logging
from uuid import uuid4
from quart import request, g


async def add_correlation_id():
    """Attach a unique correlation ID to each incoming request.

    The ID is taken from the ``X-Correlation-ID`` header if present, otherwise a
    new UUID is generated.  It is stored on ``quart.g`` so subsequent log lines
    can include the identifier, making tracing across services easier.
    """

    correlation_id = request.headers.get("X-Correlation-ID", str(uuid4()))
    g.correlation_id = correlation_id
    logging.info(
        "New request received. Correlation ID: %s, Path: %s",
        correlation_id,
        request.path,
    )