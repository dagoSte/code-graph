import threading
from tools import polish

def drive():
    threading.Thread(target=polish).start()
