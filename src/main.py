import asyncio
import uuid

import pydantic
from multiprocessing import Queue, Process
from src.interfaces import Interface, run_interface
from src.utility.command import Command, CommandType, Response
from time import sleep, time
from typing import Union
from utility.storage import InMemory
from itertools import chain
from src.model import User, Room, Invite, Guest
from utility.config import Config

""" 
    Commands:
        * current users
        * kick user
    Events:
        Quartermaster:
            * startup: sync all rooms (delete on error)
            * check user timeout (request current users first (!)) - and kick (non-roommates and non-invited are kicked right away)
                -- remember users in room.
            * on-close: kick all
            * user joins (save time)
            * clear storage (non-existent users)
        Interface:
            * user joins
            later:
                * status changes
                * invite changes
                * kicked
"""


class QuarterMaster:
    def __init__(self):
        self.config = Config()
        self.interfaces = {}
        for q in Interface.impl_list():
            send = Queue()
            receive = Queue()
            self.interfaces[q] = {
                'send_queue': send,
                'receive_queue': receive,
                'process': Process(target=run_interface, args=(q, send, receive, self.config)),
            }
            self.interfaces[q]['process'].start()
        self.command_processors = {ct: getattr(self, ct.value) for ct in CommandType}
        self._users = InMemory(User)
        self._rooms = InMemory(Room)
        self._invites = InMemory(Invite)
        self._guests = InMemory(Guest)
        self.waiting = {}
        self.__shutdown = False

    def run(self):
        asyncio.run(self.__run())  # MAIN ENTRYPOINT

    async def __run(self):
        waiter = self.config.delay_counter()
        waiter.__next__()
        waiter.send(True)
        wait_time = 0
        while not self.__shutdown:
            for i in self.interfaces:
                if not self.interfaces[i]['receive_queue'].empty():
                    asyncio.create_task(self.process_command(self.interfaces[i]['receive_queue'].get(), i))
                    wait_time = 0
            await asyncio.sleep(wait_time)
            wait_time = waiter.send(wait_time == 0)

    async def process_command(self, command: Union[Command, Response], interface: str) -> None:
        if isinstance(command, Response):
            if command.command_id in self.waiting:
                self.waiting[command.command_id] = command
            return
        try:
            data = await self.command_processors[command.command_type](command, interface)
        except Exception as e:
            resp = Response(command_id=command.command_id, data=None, error=True,
                            error_message=f"{e.__class__.__name__}: {e.__str__()}")  # fixme remove class.
        else:
            if data is None:
                resp = Response(command_id=command.command_id, data=None, error=True,
                                error_message=f"Expected Response, got None")
            else:
                resp = Response(command_id=command.command_id, data=data)
        return await self.dispatch_command(resp, interface)

    async def __dispatch_to_all(self, command: Union[Command, Response]):
        return await asyncio.gather(*[self.dispatch_command(command, i) for i in self.interfaces])

    async def dispatch_command(self, command: Union[Command, Response], interface: str, awaiting=False):
        if interface not in self.interfaces:
            raise AttributeError(f'{interface} is not a running interface')
        if awaiting:
            self.waiting[command.command_id] = None
        self.interfaces[interface]['send_queue'].put(command)
        if not awaiting:
            return
        start = time()
        while self.waiting[command.command_id] is None:
            await asyncio.sleep(self.config.response_delay)
            if time() - start > 10:
                return Response(command_id=command.command_id, error=True, error_message="Timeout on response")
        resp = self.waiting[command.command_id]
        del self.waiting[command.command_id]
        return resp


    """ EVENTS SECTION """




    """ EVENTS SECTION END """

    """ GENERAL COMMANDS SECTION """

    async def auth(self, command, interface):
        user = self._users.get(User.make_key(interface, command.key))
        if user is None:
            user = User(interface=interface, interface_id=command.key, name=command.value)
            self._users.upsert(user)
        return user

    async def rooms(self, command, interface):
        rooms = self._rooms.search(command.key, command.value)
        rooms = [q for q in rooms if
                 q.owner == command.auth.secret or command.auth.secret in q.invited or command.auth.secret in q.roommates]
        return rooms

    async def users(self, command, interface):
        if isinstance(command.value, list):
            return list(chain(*[self._users.search(command.key, q) for q in command.value]))
        return self._users.search(command.key, command.value)

    async def merge(self, command, interface):
        # 1) get current user from auth
        authcheck = self._users.get(command.auth.key())
        if authcheck is None:  # fixme write exceptions
            raise ArithmeticError(f'Fake authentication provided, how the fuck')
        # 2) get a bundle of users with the provided secret
        user_bundle = self._users.search('secret', command.value)
        # find existing userbase with the secret
        user_bundle = [q for q in user_bundle if q.interface != authcheck.interface]
        if user_bundle.__len__() == 0:
            raise ArithmeticError(f'The updated secret should match a secret of another interface!')
        user_bundle.append(command.auth)
        # 3) generate new secret
        shared_secret = uuid.uuid5(uuid.NAMESPACE_OID, ''.join(q.interface_id for q in user_bundle))
        # 4) for each user, update secret reference in rooms, save if updated, then update secret to the new one
        for user in user_bundle:
            self.update_secrets(user.secret, shared_secret)
            user.secret = shared_secret
        return shared_secret

    async def destroy(self, command, interface):  # todo: implement the call to this or delete
        if command.key is None:
            self._rooms.delete('owner', command.auth.secret)
            self._users.delete('secret', command.auth.secret)
            self._invites.delete('creator', command.auth.secret)
            return True
        to_del = self._rooms.get(command.key)
        self._rooms.delete_via_obj([to_del])
        return True

    async def create(self, command, interface):
        if command.key == 'Room':
            o = Room(**command.value)
            self._rooms.upsert(o)
        elif command.key == 'User':
            o = User(**command.value)
            self._users.upsert(o)
        else:
            raise NotImplemented(f'Tried creating {command.key}, don\'t know what that is.')
        return o

    async def edit(self, command, interface):
        if isinstance(command.value, dict):
            command.value = [command.value]
        if command.key == 'Room':
            storage = self._rooms
        elif command.key == 'User':
            storage = self._users
        resp = []
        for val in command.value:
            o = storage.get(val.get('key'))
            del val['key']
            for k in val:
                setattr(o, k, val[k])
            storage.upsert(o)
            resp.append(o)
        if resp.__len__() == 1:
            resp = resp[0]
        return resp

    async def invite(self, command, interface):
        return await self.process_invitation(command, interface)

    async def roommate(self, command, interface):
        return await self.process_invitation(command, interface)

    async def process_invitation(self, command, interface):
        if isinstance(command.key, str):
            rms = self._rooms.search('name', command.key)
            if rms.__len__() != 1:
                raise ArithmeticError(f'No room with that name')
            command.key = rms[0]
        elif isinstance(command.key, Room):
            command.key = self._rooms.get(command.key.key())
            if command.key is None:
                raise ArithmeticError(f'Room does not exist anymore')
        if command.value is None:  # add or create an Invite object
            secret = 'fdsfdsfds'  # fixme proper generation
            invite = Invite(creator=command.auth.secret, secret=secret,
                            room=command.key if command.key is None else command.key.key(),
                            roommate=command.command_type == CommandType.roommate)
            self._invites.upsert(invite)
            return invite
        else:  # find an invite object by secret code
            invite = self._invites.get(command.value)
            if invite is None:
                raise ArithmeticError('Invite not found. May be you are in the wrong interface?')
            if invite.room is not None:
                room = self._rooms.get(invite.room)
                if room is None:
                    self._invites.delete_via_obj([invite])
                    raise ArithmeticError(f'Room not found. May be it was deleted?')
                rooms = [room]
            else:
                rooms = self._rooms.search('owner', invite.creator)
            for room in rooms:
                if invite.roommate:
                    if command.auth.secret not in room.roommates:
                        room.roommates.append(command.auth.secret)
                else:
                    if command.auth.secret not in room.invited:
                        room.invited.append(command.auth.secret)
                self._invites.delete_via_obj([invite])
            owner_name = self._users.search('secret', invite.creator)
            return {'owner': owner_name, 'rooms': [q.name for q in rooms], 'status': 'roommate' if invite.roommate else 'guest'}

    async def invite_clear(self, command, interface):
        invites = self._invites.search('creator', command.auth.secret)
        self._invites.delete_via_obj(invites)
        return True

    async def evict(self, command, interface):
        if isinstance(command.key, str):
            room = self._rooms.get(command.key)
            if room is None:
                rms = self._rooms.search('name', command.key)
                if rms.__len__() != 1:
                    raise ArithmeticError(f'No room with that name')
                command.key = rms[0]
            else:
                command.key = room
        elif isinstance(command.key, Room):
            command.key = self._rooms.get(command.key.key())
            if command.key is None:
                raise ArithmeticError(f'Room does not exist anymore')
        user = self._users.search('name', command.value)
        if user.__len__() != 1:
            raise ArithmeticError(f'User {user} not found.')
        user = user[0]
        if command.key is None:
            rooms = self._rooms.search_func(lambda room: room.owner == command.auth.secret and
                                                         (user.secret in room.roommates or user.secret in room.invited))
        else:
            rooms = [command.key]
        for room in rooms:
            if user.secret in room.invited:
                room.invited.remove(user.secret)
            if user.secret in room.roommates:
                room.roommates.remove(user.secret)
            self._rooms.upsert(room)
        return True

    async def shutdown(self, command, interface):
        await self.__dispatch_to_all(Command(command_type=CommandType.shutdown))
        for q in Interface.impl_list():
            i = self.interfaces.get(q)
            if i is None:
                raise RuntimeError(f'Process for interface {q} not found')
            i['process'].join()
            self.__shutdown = True

    async def save(self, command, interface):
        return True

    """ GENERAL COMMANDS SECTION END """

    def update_secrets(self, old_secret: pydantic.UUID5, new_secret: pydantic.UUID5):
        rooms = self._rooms.search_func(
            lambda o: o.owner == old_secret or old_secret in o.invited or old_secret in o.roommates
        )
        for room in rooms:
            if room.owner == old_secret:
                room.owner = new_secret
            if old_secret in room.invited:
                room.invited.pop(room.invited.idnex(old_secret))
                room.invited.append(new_secret)
            if old_secret in room.roommates:
                room.roommates.pop(room.roommates.index(old_secret))
                room.roommates.append(new_secret)






if __name__ == '__main__':
    q = QuarterMaster()
    # u1 = User(interface='telegram', interface_id=1, secret=uuid.uuid5(uuid.NAMESPACE_OID, '1'))
    # u2 = User(interface='telegram', interface_id=2, secret=uuid.uuid5(uuid.NAMESPACE_OID, '2'))
    # q._users.upsert(u1)
    # q._users.upsert(u2)
    # q._rooms.upsert(Room(interface='telegram', name='hello', interface_id=1, owner=u1.secret, address=''))
    # c = Command(command_type=CommandType.merge, auth=u2, value=u1.secret.__str__())
    # print(q.merge(c, 'telegram'))
    q.run()
