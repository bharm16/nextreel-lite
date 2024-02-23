# cpu_profiler.py
import cProfile
import pstats
import io
from functools import wraps

def cpu_profile(func):
    """
    A decorator that profiles the CPU usage of the decorated function.
    """
    @wraps(func)
    def profiled_func(*args, **kwargs):
        profiler = cProfile.Profile()
        profiler.enable()

        result = func(*args, **kwargs)

        profiler.disable()
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
        ps.print_stats()

        print(s.getvalue())
        return result

    return profiled_func
