import asyncio
import cProfile
import pstats
import io
from functools import wraps
from memory_profiler import memory_usage

def cpu_profile(func):
    """
    A decorator that profiles the CPU usage of the decorated function.
    It supports both synchronous and asynchronous functions.
    """
    @wraps(func)
    def wrapped_sync(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            result = func(*args, **kwargs)
        finally:
            profiler.disable()
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats()
            with open('cpu_profile_sync.txt', 'w') as file:
                file.write(s.getvalue())
        return result

    @wraps(func)
    async def wrapped_async(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()
        try:
            result = await func(*args, **kwargs)
        finally:
            profiler.disable()
            s = io.StringIO()
            ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
            ps.print_stats()
            with open('cpu_profile_async.txt', 'w') as file:
                file.write(s.getvalue())
        return result

    if asyncio.iscoroutinefunction(func):
        return wrapped_async
    else:
        return wrapped_sync

def memory_profile(func):
    """
    A decorator that profiles the memory usage of the decorated function.
    It supports both synchronous and asynchronous functions.
    """
    @wraps(func)
    def wrapped_sync(*args, **kwargs):
        mem_usage_before = memory_usage(-1)[0]
        result = func(*args, **kwargs)
        mem_usage_after = memory_usage(-1)[0]
        with open('memory_profile_sync.txt', 'w') as file:
            file.write(f"Memory increased by: {mem_usage_after - mem_usage_before} MiB\n")
        return result

    @wraps(func)
    async def wrapped_async(*args, **kwargs):
        mem_usage_before = memory_usage(-1)[0]
        result = await func(*args, **kwargs)
        mem_usage_after = memory_usage(-1)[0]
        with open('memory_profile_async.txt', 'w') as file:
            file.write(f"Memory increased by: {mem_usage_after - mem_usage_before} MiB\n")
        return result

    if asyncio.iscoroutinefunction(func):
        return wrapped_async
    else:
        return wrapped_sync


# Example usage of the decorators
if __name__ == "__main__":

    @cpu_profile
    def some_cpu_intensive_function():
        print("Running some CPU-intensive task.")
        for i in range(10000):
            _ = i ** 2
        print("Finished CPU task.")


    @cpu_profile
    async def some_cpu_intensive_async_function():
        print("Running some CPU-intensive async task.")
        for i in range(10000):
            _ = i ** 2
        print("Finished CPU async task.")


    @memory_profile
    def some_memory_intensive_function():
        print("Running some memory-intensive task.")
        large_list = [i for i in range(1000000)]
        print("Finished memory task.")


    @memory_profile
    async def some_memory_intensive_async_function():
        print("Running some memory-intensive async task.")
        large_list = [i for i in range(1000000)]
        print("Finished memory async task.")


    # Synchronous functions
    some_cpu_intensive_function()
    some_memory_intensive_function()

    # Asynchronous functions
    asyncio.run(some_cpu_intensive_async_function())
    asyncio.run(some_memory_intensive_async_function())
