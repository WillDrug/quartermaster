

class Config:
    delay_times = [1, 3, 5]
    shutdown_delay = 5
    response_delay = 1
    master_ids = ['Telegram391834810']

    def delay_counter(self):
        idx = 0
        while True:
            a = yield self.delay_times[idx]
            if a:
                idx = 0
            elif idx < self.delay_times.__len__() - 1:
                idx += 1