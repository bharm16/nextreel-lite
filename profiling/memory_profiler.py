# memory_profiler.py
import asyncio
from functools import wraps

from memory_profiler import memory_usage, profile


def memory_profile(func):
    """
    A decorator that profiles the memory usage of the decorated function.
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        mem_usage_before = memory_usage(-1)[0]
        result = await func(*args, **kwargs)
        mem_usage_after = memory_usage(-1)[0]
        print(f"Memory increased by: {mem_usage_after - mem_usage_before} MiB")
        return result

    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    else:
        return profile(func)
