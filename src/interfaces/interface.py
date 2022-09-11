import asyncio
from abc import ABCMeta, abstractmethod
from time import sleep, time
import threading
from multiprocessing import Queue
from utility.command import CommandType, Command, Response
from model import *


class Interface(metaclass=ABCMeta):
    @classmethod
    def impl_list(cls):
        return {q.__name__: q for q in cls.__subclasses__()}

    def __init__(self, send_queue: Queue, receive_queue: Queue, config, *args, **kwargs):
        """
        :param send_queue: SEND from the perspecitve of the caller
        :param receive_queue: RECEIVE from the perspective of the caller
        :param args:
        :param kwargs:
        """
        self.activity = {}
        self.config = config
        self.rec_queue = send_queue
        self.send_queue = receive_queue
        self._shutdown = False
        self.threads = [threading.Thread(daemon=True, target=self.process_commands),
                        threading.Thread(daemon=True, target=self.initialize)]
        self.waiting = {}
        self.loop = asyncio.get_event_loop()
        self.last_sync = time.time()

    async def process_command(self, command: Union[Command, Response]):
        if isinstance(command, Response):
            if command.command_id in self.waiting:
                if self.waiting[command.command_id] is None:
                    self.waiting[command.command_id] = command
                elif callable(self.waiting[command.command_id]):
                    return self.waiting[command.command_id](command)
        if isinstance(command, Command):
            if command.command_type == CommandType.evict:
                resp = await self.kick(command)
            elif command.command_type == CommandType.users:
                resp = await self.local_users(command)
            if isinstance(resp, Response):
                return await self._dispatch_command(resp, awaiting=False)

    @abstractmethod
    async def kick(self, command):
        pass

    @abstractmethod
    async def local_users(self, command):
        pass

    def dispatch_command(self, command: Union[Command, Response, list], awaiting=True):  # asyncio bridge
        resp = self.loop.create_task(self._dispatch_command(command, awaiting=awaiting))
        while not resp.done():
            sleep(self.config.polling_delay)  # presuming 100% completion due to the timer inside
        return resp.result()

    async def _dispatch_command(self, command: Union[Command, Response, list], awaiting=True):
        if not isinstance(command, list):
            command = [command]
        for c in command:
            if awaiting:
                self.waiting[c.command_id] = None
            self.send_queue.put(c)
        ids = [c.command_id for c in command]
        resp = {}
        if awaiting:
            start = time.time()
            while any(self.waiting[q] is None for q in ids):
                await asyncio.sleep(self.config.response_delay)
                if time.time() - start > 10:
                    for c in command:
                        if self.waiting[c.command_id] is None:
                            self.waiting[c.command_id] = Response(
                                command_id=c.command_id, error=True, error_message="Timeout on response"
                            )
            for c in command:
                resp[c.command_id] = self.waiting[c.command_id]
                del self.waiting[c.command_id]
        if resp.keys().__len__() == 1:
            resp = resp[command[0].command_id]
        return resp

    def shutdown(self):
        self.local_shutdown()
        for t in self.threads:
            if t.is_alive():
                t.join()

    @abstractmethod
    def local_shutdown(self):
        pass

    @abstractmethod
    def initialize(self):
        pass

    def process_commands(self):
        self.loop.run_until_complete(self.__process_commands())

    async def __process_commands(self):
        processed = False
        waiter = self.config.delay_counter()
        waiter.__next__()
        waiter.send(True)
        self.last_sync = time.time()
        while not self._shutdown:
            if not self.rec_queue.empty():
                command = self.rec_queue.get()
                if isinstance(command, Command):
                    if command.command_type == CommandType.shutdown:
                        self._shutdown = True
                        continue
                asyncio.create_task(self.process_command(command))
                processed = True
            if time.time() - self.last_sync > self.config.sync_delay:
                self.last_sync = time.time()
                await self.async_sync()
            await asyncio.sleep(waiter.send(processed))
            processed = False

    def run(self):
        for thread in self.threads:
            thread.start()
        while not self._shutdown:
            sleep(self.config.shutdown_delay)
        self.shutdown()

    """command section"""
    async def async_sync(self):
        active = self.activity
        self.activity = {}
        return await self._dispatch_command(Command(command_type=CommandType.sync, value=active), awaiting=False)

    def sync(self):
        active = self.activity
        self.activity = {}
        return self.dispatch_command(Command(command_type=CommandType.sync, value=active), awaiting=False)

    def home(self, auth):
        return self.dispatch_command(Command(command_type=CommandType.homes, auth=auth, value=True))

    def _save_activity(self, user_id, username, room_id):
        if room_id not in self.activity:
            self.activity[room_id] = {}
        self.activity[room_id][user_id] = {'name': username, 'active': time.time()}

    def _user_joins(self, user_id, username, room_id):
        self._save_activity(user_id, username, room_id)
        return self.dispatch_command(Command(command_type=CommandType.join, key=room_id,
                                             value={'name': username, 'user_id': user_id}), awaiting=False)

    def _user_leaves(self, user_id, room_id):
        return self.dispatch_command(Command(command_type=CommandType.leave, key=room_id, value=user_id), awaiting=False)

    def auth(self, user_id, user_name):
        command = Command(command_type=CommandType.auth, key=user_id, value=user_name)
        return self.dispatch_command(command, awaiting=True)

    def get_visible_homes(self, auth):
        return self.dispatch_command(Command(command_type=CommandType.homes, auth=auth))

    def get_current_home(self, auth, interface_id):
        return self.dispatch_command(Command(command_type=CommandType.homes, auth=auth, value=interface_id))

    def get_home(self, auth, home_id):
        return self.dispatch_command(Command(command_type=CommandType.homes, auth=auth, key='owner', value=home_id))

    def get_home_rooms(self, auth, home):  # auth to run
        return self.dispatch_command(Command(command_type=CommandType.rooms, key=None, value=home, auth=auth))

    def get_own_rooms(self, auth, name):
        """
        Get rooms of the user
        :param auth: User requesting
        :param name: Specific room name or None for all
        :return: Respones object (.data has either a list or a Room object)
        """
        if name is None:
            command = Command(command_type=CommandType.rooms, auth=auth, key="owner", value=auth.secret)
        else:
            command = Command(command_type=CommandType.rooms, auth=auth, key="name", value=name)
        resp = self.dispatch_command(command)
        resp.data = [q for q in resp.data if q.owner == auth.secret]  # filter only own rooms
        return resp

    def merge_users(self, auth, new_secret):
        command = Command(command_type=CommandType.merge, auth=auth, value=new_secret)
        return self.dispatch_command(command)

    def destroy(self, auth, room=None):
        command = Command(command_type=CommandType.destroy, auth=auth, key=room)
        return self.dispatch_command(command)

    def create_room(self, auth, room_name, address, room_id):
        val = {
            'name': room_name,
            'address': address,
            'interface_id': room_id,
            'interface': self.__class__.__name__,
            'owner': auth.secret
        }
        command = Command(command_type=CommandType.create, auth=auth, key='Room', value=val)
        return self.dispatch_command(command)

    def edit(self, auth, key, changes):
        if any('key' not in q for q in (changes if isinstance(changes, list) else [changes])):
            raise KeyError(f'Edit command must contain key')
        return self.dispatch_command(Command(command_type=CommandType.edit, auth=auth, key=key, value=changes))

    def invite(self, auth, username, can_use_invite=False, roommate=False):
        home = self.home(auth)
        if home.error:
            return home
        home = home.data
        users = self.dispatch_command(Command(command_type=CommandType.users, key='name', value=username))
        if users.error:  # error'd
            return users
        else:
            users = users.data
        if users.__len__() > 1:
            users = [q for q in users.data if q.interface == self.__class__.__name__]  # username in THIS interface
        if users.__len__() != 1 and not can_use_invite:
            return Response(command_id=0, error=True, error_message='Did not manage to zero in on the user')
        elif users.__len__() != 1:
            invite = self.dispatch_command(Command(
                                                command_type=CommandType.roommate if roommate else CommandType.invite,
                                                auth=auth,
                                                key=home
            ))
            return invite
        # got user, can add
        user = users.pop()
        if roommate:
            res = home.roommates
        else:
            res = home.invited
        if user.secret not in res:
            res.append(user.secret)
        resp = self.dispatch_command(Command(command_type=CommandType.edit, key='Home',
                                             value={'key': home.key(),
                                                    'roommates' if roommate else 'invited': res}))
        return resp


    def use_invite(self, auth, secret):
        return self.dispatch_command(Command(command_type=CommandType.invite, key=None, value=secret, auth=auth))

    def evict(self, auth, username):
        return self.dispatch_command(Command(command_type=CommandType.evict, value=username, auth=auth))

    def users(self, key, value):
        return self.dispatch_command(Command(command_type=CommandType.users, key=key, value=value))

    def clearinvites(self, auth):
        return self.dispatch_command(Command(command_type=CommandType.invite_clear, auth=auth))
