"""Enqueue runtime maintenance jobs into ARQ."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from logging_config import get_logger

logger = get_logger(__name__)


async def main() -> int:
    try:
        from arq import create_pool
        from arq.connections import RedisSettings
    except ImportError:
        logger.error("arq is not installed")
        return 1

    parser = argparse.ArgumentParser(description="Enqueue a runtime maintenance job")
    parser.add_argument("job", help="ARQ function name to enqueue")
    parser.add_argument("args", nargs="*", help="Optional positional arguments for the job")
    parsed = parser.parse_args()

    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    try:
        await pool.enqueue_job(parsed.job, *parsed.args)
        logger.info("Enqueued %s with args=%s", parsed.job, parsed.args)
        return 0
    finally:
        await pool.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
