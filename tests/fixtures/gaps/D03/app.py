def polish():
    return 1


def run(fn):
    return fn()


def drive(polish):
    return run(polish)
