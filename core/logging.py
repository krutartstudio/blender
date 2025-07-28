def log_fn_call(func):
    def inner(*args, **kwargs):
        print(f"\nStarting {func.__name__}, args: {args}, kwargs {kwargs}")
        result = func(*args, **kwargs)
        print(f"Finished {func.__name__}, res: {result}")
        return result
    return inner
