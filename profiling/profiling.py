# profiling.py
import cProfile
import pstats
import io
from functools import wraps


def cpu_profile(func):
    """
    A decorator that uses cProfile to profile a function
    """

    @wraps(func)
    def profiled_func(*args, **kwargs):
        # Create a cProfile object and enable it
        profiler = cProfile.Profile()
        profiler.enable()

        # Run the function being profiled
        result = func(*args, **kwargs)

        # Disable the profiler and print out the stats
        profiler.disable()

        # Create a stream to capture the stats
        s = io.StringIO()
        sortby = 'cumulative'  # Sort the output by cumulative time spent in each function
        ps = pstats.Stats(profiler, stream=s).sort_stats(sortby)
        ps.print_stats()

        # Print the profiled statistics to stdout
        print(s.getvalue())

        # Return the function's original result
        return result

    return profiled_func


def memory_profile(func):
    """
    A decorator that uses memory_profiler to profile memory usage of a function
    """

    @wraps(func)
    def profiled_func(*args, **kwargs):
        # Define a wrapper to execute the function and return its result
        def wrapper():
            return func(*args, **kwargs)

        # Profile the memory usage of the wrapper function
        mem_usage = memory_usage(wrapper)

        # Print the memory usage statistics
        print(f"Memory usage (in kilobytes): {mem_usage}")

        # Return the result of the function call from within the wrapper
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
