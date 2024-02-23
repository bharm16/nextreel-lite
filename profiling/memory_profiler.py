# memory_profiler.py

# The memory_profiler library provides a decorator already, so you can import it directly.
# However, if you want to customize it or add additional functionality, you can define your own decorator.

# Here's an example of a custom decorator using memory_profiler:
from functools import wraps

from scss.util import profile


def memory_profile(func):
    """
    A decorator that profiles the memory usage of the decorated function.
    """
    # Apply the memory_profiler's profile decorator
    profiled_func = profile(func)

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = profiled_func(*args, **kwargs)
        return result

    return wrapper
