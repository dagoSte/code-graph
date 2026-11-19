import handlers


def drive(event):
    return getattr(handlers, "on_" + event)()
