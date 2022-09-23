from .interface import Interface
from .telegram import Telegram
from .qdiscord import Discord

def run_interface(cls, send, receive, config):
    interfaces = Interface.impl_list()
    if cls not in interfaces:
        raise RuntimeError(f'Interface {cls} not found')
    i = interfaces[cls](send, receive, config)
    i.run()