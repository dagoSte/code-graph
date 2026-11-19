import handlers
from registry import dispatch

def drive():
    return dispatch('start')
