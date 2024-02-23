# profiling.py
import asyncio
import cProfile
import pstats
import io
from functools import wraps
from memory_profiler import memory_usage

def cpu_profile(func):
    @wraps(func)
    async def profiled_func(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()

        # Run the coroutine being profiled
        result = await func(*args, **kwargs)

        profiler.disable()
        s = io.StringIO()
        sortby = 'cumulative'
        ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
        ps.print_stats()
        print(s.getvalue())

        return result

    if asyncio.iscoroutinefunction(func):
        return profiled_func
    else:
        return func

def memory_profile(func):
    @wraps(func)
    async def profiled_func(*args, **kwargs):
        mem_usage_before = memory_usage(-1)[0]
        result = await func(*args, **kwargs)
        mem_usage_after = memory_usage(-1)[0]
        print(f"Memory usage: {mem_usage_after - mem_usage_before} MiB")

        return result

    if asyncio.iscoroutinefunction(func):
        return profiled_func
    else:
        return func



# Example usage of the decorators
if __name__ == "__main__":

    @cpu_profile
    def some_cpu_intensive_function():
        # Example CPU-intensive code here
        print("Running some CPU-intensive task.")
        for i in range(10000):
            _ = i ** 2
        return "Finished CPU task."


    @memory_profile
    def some_memory_intensive_function():
        # Example memory-intensive code here
        print("Running some memory-intensive task.")
        large_list = [i for i in range(1000000)]
        return "Finished memory task."


    # Call the functions to see the profiling in action
    some_cpu_intensive_function()
    some_memory_intensive_function()
