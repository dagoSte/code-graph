def __getattr__(name):
    from . import impl
    return getattr(impl, name)
