from time import time

class Config:
    delay_times = [0, 0.1, 0.5, 1, 3, 5]
    delay_switch = [3, 10, 30, 60*2, 60*5, 60*10]
    shutdown_delay = 5
    response_delay = 1
    polling_delay = 1
    master_ids = ['Telegram391834810']

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
