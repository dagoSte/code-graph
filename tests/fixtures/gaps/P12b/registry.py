COMMANDS = {}

def command(key):
    def deco(fn):
        COMMANDS[key] = fn
        return fn
    return deco

def dispatch(key):
    return COMMANDS[key]()
