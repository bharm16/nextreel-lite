# profiling.py
import cProfile
import pstats
import io
from functools import wraps

from memory_profiler import memory_usage


def cpu_profile(func):
    """
    A decorator that uses cProfile to profile a function.
    """

    @wraps(func)
    def profiled_func(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()

        result = func(*args, **kwargs)  # Run the function being profiled

        profiler.disable()
        s = io.StringIO()
        sortby = 'cumulative'
        ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
        ps.print_stats()
        print(s.getvalue())  # Print the profiled statistics to stdout

        return result

    return profiled_func


def memory_profile(func):
    """
    A decorator that profiles the memory usage of the decorated function.
    """

    @wraps(func)
    def profiled_func(*args, **kwargs):
        def wrapper():
            return func(*args, **kwargs)

        mem_usage = memory_usage(wrapper)  # Profile the memory usage
        print(f"Memory usage (in kilobytes): {mem_usage}")
        return wrapper()

    return profiled_func


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
