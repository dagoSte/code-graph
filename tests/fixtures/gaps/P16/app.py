try:
    from fastimpl import polish
except ImportError:
    from slowimpl import polish

def drive():
    return polish()
