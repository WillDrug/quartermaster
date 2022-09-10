from time import time
from os import getenv

class Config:
    delay_times = [0, 0.1, 0.5, 1, 3, 5]
    delay_switch = [3, 10, 30, 60*2, 60*5, 60*10]
    shutdown_delay = 5
    response_delay = 1
    polling_delay = 1
    sync_delay = 30
    master_ids = ['Telegram391834810']
    telegram_auth = ''

    def delay_counter(self):
        idx = 0
        curtime = time()
        while True:
            a = yield self.delay_times[idx]
            if a:
                idx = 0
                curtime = time()
            else:
                for i, tst in enumerate(self.delay_switch):
                    if curtime - time() > tst:
                        idx = i
                    else:
                        break

    def __init__(self):
        for attr in self.__dir__():  # you can break this by overriding a dunder method. don't do that.
            if getenv(attr):  # fixme: do a better job here
                setattr(self, attr, getenv(attr))
