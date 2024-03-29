# import asyncio
# import cProfile
# import pstats
# import io
# from functools import wraps
#
# from memory_profiler import memory_usage
#
# # Update this path to your actual desktop path
# desktop_path = '/Users/bryceharmon/Desktop/'
#
#
# # Clear profiling files at the start of the program
# def clear_profiling_files():
#     files = ['cpu_profile_sync.txt', 'cpu_profile_async.txt', 'memory_profile_sync.txt', 'memory_profile_async.txt']
#     for file in files:
#         with open(desktop_path + file, 'w') as f:
#             pass  # Clear the file
#
#
# # Call this function once at the start of your program
# clear_profiling_files()
#
#
# def cpu_profile(func):
#     @wraps(func)
#     def wrapped_sync(*args, **kwargs):
#         profiler = cProfile.Profile()
#         profiler.enable()
#         try:
#             result = func(*args, **kwargs)
#         finally:
#             profiler.disable()
#             s = io.StringIO()
#             ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
#             ps.print_stats()
#             with open(desktop_path + 'cpu_profile_sync.txt', 'a') as file:  # Append to file
#                 file.write(s.getvalue() + '\n\n')
#         return result
#
#     @wraps(func)
#     async def wrapped_async(*args, **kwargs):
#         profiler = cProfile.Profile()
#         profiler.enable()
#         try:
#             result = await func(*args, **kwargs)
#         finally:
#             profiler.disable()
#             s = io.StringIO()
#             ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
#             ps.print_stats()
#             with open(desktop_path + 'cpu_profile_async.txt', 'a') as file:  # Append to file
#                 file.write(s.getvalue() + '\n\n')
#         return result
#
#     if asyncio.iscoroutinefunction(func):
#         return wrapped_async
#     else:
#         return wrapped_sync
#
#
# def memory_profile(func):
#     @wraps(func)
#     def wrapped_sync(*args, **kwargs):
#         mem_usage_before = memory_usage(-1)[0]
#         result = func(*args, **kwargs)
#         mem_usage_after = memory_usage(-1)[0]
#         with open(desktop_path + 'memory_profile_sync.txt', 'a') as file:  # Append to file
#             file.write(f"Memory increased by: {mem_usage_after - mem_usage_before} MiB\n\n")
#         return result
#
#     @wraps(func)
#     async def wrapped_async(*args, **kwargs):
#         mem_usage_before = memory_usage(-1)[0]
#         result = await func(*args, **kwargs)
#         mem_usage_after = memory_usage(-1)[0]
#         with open(desktop_path + 'memory_profile_async.txt', 'a') as file:  # Append to file
#             file.write(f"Memory increased by: {mem_usage_after - mem_usage_before} MiB\n\n")
#         return result
#
#     if asyncio.iscoroutinefunction(func):
#         return wrapped_async
#     else:
#         return wrapped_sync
#
#
# # Example usage of the decorators
# if __name__ == "__main__":
#
#     @cpu_profile
#     def some_cpu_intensive_function():
#         print("Running some CPU-intensive task.")
#         for i in range(10000):
#             _ = i ** 2
#         print("Finished CPU task.")
#
#
#     @cpu_profile
#     async def some_cpu_intensive_async_function():
#         print("Running some CPU-intensive async task.")
#         for i in range(10000):
#             _ = i ** 2
#         print("Finished CPU async task.")
#
#
#     @memory_profile
#     def some_memory_intensive_function():
#         print("Running some memory-intensive task.")
#         large_list = [i for i in range(1000000)]
#         print("Finished memory task.")
#
#
#     @memory_profile
#     async def some_memory_intensive_async_function():
#         print("Running some memory-intensive async task.")
#         large_list = [i for i in range(1000000)]
#         print("Finished memory async task.")
#
#
#     # Synchronous functions
#     some_cpu_intensive_function()
#     some_memory_intensive_function()
#
#     # Asynchronous functions
#     asyncio.run(some_cpu_intensive_async_function())
#     asyncio.run(some_memory_intensive_async_function())
