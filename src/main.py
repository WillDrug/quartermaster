import asyncio
import uuid
import random
import pydantic
from multiprocessing import Queue, Process
from interfaces import Interface, run_interface
from utility.command import Command, CommandType, Response
from time import sleep, time
from typing import Union
from utility.storage import InMemory, ShelveStorage
from itertools import chain
from model import User, Room, Invite, Guest, Home
from utility.config import Config
import html

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
# todo: failure count
# todo: destroy on failure number
# todo: LOG THIS BITCH UP

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
        self._users = ShelveStorage(User)
        self._homes = ShelveStorage(Home)
        self._rooms = ShelveStorage(Room)
        self._invites = ShelveStorage(Invite)
        self._guests = ShelveStorage(Guest)
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
            print(f'on {command}: {e.__class__}: {e.__str__()}')  # todo logs
            message = f"{e.__class__.__name__}: {e.__str__()}"
            message = html.escape(message)
            resp = Response(command_id=command.command_id, data=None, error=True,
                            error_message=message)  # fixme remove class.
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
            return Response(command_id=command.command_id, data=True)
        start = time()
        while self.waiting[command.command_id] is None:
            await asyncio.sleep(self.config.response_delay)
            if time() - start > 10:
                del self.waiting[command.command_id]
                return Response(command_id=command.command_id, error=True, error_message="Timeout on response")
        resp = self.waiting[command.command_id]
        del self.waiting[command.command_id]
        return resp

    """ EVENTS SECTION """

    async def generate_events(self):
        return self.generate_room_commands()

    async def generate_room_commands(self, rooms=None):
        if rooms is None:
            rooms = self._rooms.search_func(lambda o: True)  # sync rooms. sync users (remove unused).
        return await asyncio.gather(*[self.room_events(room) for room in rooms])

    async def room_events(self, room):  # go over sync time, and kick, remove
        home = self._homes.get(room.owner)
        if home is None:
            raise ArithmeticError(f'Cannot find a home for {room.owner.__str__()} for some reason.')
        if home.closed:  # closed room: all but the roommates and the owner are kicked
            searcher = lambda o: room.key() == o.room and o.user not in home.roommates and o.user != room.owner
        elif home.invited:
            searcher = lambda o: room.key() == o.room and o.user not in home.roommates and o.user != room.owner and (
                    o.user not in home.invited or time() - o.last_sync > home.timeout)
        else:
            searcher = lambda \
                    o: room.key() == o.room and o.user not in home.roommates and o.user != room.owner \
                       and time() - o.last_sync > home.timeout

        guests = self._guests.search_func(searcher)
        guests_secrets = [q.user for q in guests]
        users = self._users.search_func(lambda o: o.secret in guests_secrets)
        if users.__len__() == 0:
            return True
        self._guests.delete_via_obj(guests)
        c = Command(command_type=CommandType.evict, key=room.interface_id, value=users)  # dispatch kick command
        resp = await self.dispatch_command(c, room.interface, awaiting=True)
        if resp.error:
            print(f'Failed to kick: {resp.error_message}')
            return False
        return True

    async def user_events(self, user):  # no user events yet.
        return

    """ EVENTS SECTION END """

    """ GENERAL COMMANDS SECTION """

    async def join(self, command, interface):
        user = self._users.get(User.make_key(interface, command.value['user_id']))
        if user is None:
            user = User(interface=interface, interface_id=command.value['user_id'],
                        name=command.value['name'])
            self._users.upsert(user)
        room = self._rooms.get(Room.make_key(interface, command.key))
        if room is None:
            return
        home = self._homes.get(room.owner)
        kick = False
        if user.secret != home.owner:
            if home.locked and user.secret not in home.invited and user.secret not in home.roommates:
                kick = True
            if home.closed and user.secret not in home.roommates:
                kick = True
        if kick:
            return await self.dispatch_command(Command(command_type=CommandType.evict, key=room.interface_id,
                                                       value=[user]), interface, awaiting=False)
        # get other interfaces if present
        guests = self._guests.search('user', user.secret)
        additional_kick = {}
        for guest in guests:
            test_room = self._rooms.get(guest.room)
            if test_room.interface_id != room.interface_id:
                if test_room.interface not in additional_kick:
                    additional_kick[test_room.interface] = []
                additional_kick[test_room.interface].append(test_room.interface_id)
        for i in additional_kick:
            for iid in additional_kick[i]:
                asyncio.create_task(self.dispatch_command(Command(command_type=CommandType.evict, key=iid,
                                                                  value=[user]), i, awaiting=False))

        self._guests.upsert(Guest(user=user.secret, room=room.key(), last_sync=time()))

    async def leave(self, command, interface):
        user = self._users.get(User.make_key(interface, command.value))
        if user is None:
            raise ArithmeticError(f'User not found')
        room = self._rooms.get(Room.make_key(interface, command.key))
        if room is None:
            raise ArithmeticError(f'Room not found')
        guest = self._guests.get(Guest.make_key(user.secret, room.key()))
        self._guests.delete_via_obj([guest])
        return

    async def sync(self, command, interface):
        for room_id in command.value:  # user activity upsert from entire interface
            room = self._rooms.get(Room.make_key(interface, room_id))
            if room is None:
                continue
            for user_id in command.value[room_id]:
                user = self._users.get(User.make_key(interface, user_id))
                if user is None:
                    user = User(interface=interface, interface_id=user_id, name=command.value[room_id][user_id]['name'])
                    self._users.upsert(user)

                guest = self._guests.get(Guest.make_key(user.secret, room.key()))
                if guest is not None:
                    guest.last_sync = command.value[room_id][user_id]['active']
                else:
                    guest = Guest(user=user.secret, room=room.key(),
                                  last_sync=command.value[room_id][user_id]['active'])
                    self._guests.upsert(guest)
        # events generation is part of sync
        return await self.generate_room_commands(rooms=self._rooms.search('interface', interface))

    async def auth(self, command, interface):
        user = self._users.get(User.make_key(interface, command.key))
        if user is None:
            user = User(interface=interface, interface_id=command.key, name=command.value)
            home = self._homes.get(user.secret)
            if home is None:
                self._homes.upsert(Home(owner=user.secret, closed=True))
            self._users.upsert(user)

        return user

    async def rooms(self, command, interface):
        if command.key is None:  # value is homeowner
            home = self._homes.get(command.value)
            if home is None:
                raise ArithmeticError(f'No home for the key provided')
            if not ((home.locked and (command.auth.secret in home.roommates or command.auth.secret in home.invited))\
                    or (home.closed and (command.auth.secret in home.roommates)) or \
                    command.auth.secret == home.owner or (not home.closed and not home.locked)):
                raise ArithmeticError(f'Not allowed to view.')
            rooms = self._rooms.search('owner', command.value)
        else:  # todo also auth?
            rooms = self._rooms.search(command.key, command.value)
        return rooms

    async def users(self, command, interface):
        if isinstance(command.value, list):
            return list(chain(*[self._users.search(command.key, q) for q in command.value]))
        return self._users.search(command.key, command.value)

    async def homes(self, command, interface):
        filter_lambda = lambda home: (home.locked and
                                      (command.auth.secret in home.roommates or command.auth.secret in home.invited))\
                                     or (home.closed and (command.auth.secret in home.roommates)) or \
                                     command.auth.secret == home.owner or (not home.closed and not home.locked)
        if command.key is None and command.value is None:  # get available
            return self._homes.search_func(filter_lambda)
        elif command.key == 'owner':
            homes = [self._homes.get(command.value)]
        elif command.value is not None:  # interface id
            room = self._rooms.get(Room.make_key(interface, command.value))
            if room is None:
                raise ArithmeticError(f'This room is not managed')
            homes = self._homes.get(room.owner)
        else:
            homes = self._homes.search(command.key, command.value)
        if not isinstance(homes, list):
            homes = [homes]
        homes = list(filter(filter_lambda, homes))
        return homes

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
        # everything's fine, time to kill interfaces
        for user in user_bundle:
            await self.dispatch_command(Command(command_type=CommandType.merge, key=user.interface_id, value='*'), user.interface)
        # 3) generate new secret
        shared_secret = uuid.uuid5(uuid.NAMESPACE_OID, ''.join(q.interface_id for q in user_bundle))
        # 4) for each user, update secret reference in rooms, save if updated, then update secret to the new one
        own_homes = self._homes.search_func(lambda o: o.owner in [q.secret for q in user_bundle])
        new_home = Home(
             owner=shared_secret,
             timeout=max([q.timeout for q in own_homes]),
             locked=any([q.locked for q in own_homes]),
             closed=any([q.closed for q in own_homes]),
             invited=list(set(chain(*[q.invited for q in own_homes]))),
             roommates=list(set(chain(*[q.roommates for q in own_homes])))
        )
        self._homes.delete_via_obj(own_homes)
        self._homes.upsert(new_home)
        for user in user_bundle:
            self.update_secrets(user.secret, shared_secret)
            user.secret = shared_secret
            self._users.upsert(user)
            await self.dispatch_command(Command(command_type=CommandType.merge, key=user.interface_id, value=user), user.interface)
        return shared_secret

    async def destroy(self, command, interface):  # todo: implement the call to this or delete
        if command.key is None:  # do not deleting remembered user as they can still be a guest somewhere.
            rooms = self._rooms.search('owner', command.auth.secret)
            for room in rooms:
                self._guests.delete_via_attr('room', room.key())
            self._rooms.delete_via_obj(rooms)
            self._invites.delete_via_attr('creator', command.auth.secret)
            self._homes.delete(command.auth.secret)
            return True
        self._rooms.delete(command.key)
        return True

    async def create(self, command, interface):
        if command.key == 'Room':
            o = Room(**command.value)
            if self._rooms.get(o.key()) is not None:
                raise ArithmeticError(f'This room is already managed (!)')
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
            events_func = self.room_events
        elif command.key == 'User':
            storage = self._users
            events_func = self.user_events
        elif command.key == 'Home':
            storage = self._homes
            events_func = self.room_events
        else:
            raise ArithmeticError(f'Unknown key to edit')
        resp = []
        for val in command.value:
            o = storage.get(val.get('key'))
            del val['key']
            for k in val:
                setattr(o, k, val[k])
            storage.upsert(o)
            resp.append(o)
            await events_func(o)
        if resp.__len__() == 1:
            resp = resp[0]
        return resp

    async def invite(self, command, interface):
        return await self.process_invitation(command, interface)

    async def roommate(self, command, interface):
        return await self.process_invitation(command, interface)

    async def process_invitation(self, command, interface):
        if command.value is None:  # add or create an Invite object
            secret = uuid.uuid5(uuid.NAMESPACE_OID, random.random().__str__()).__str__()
            invite = Invite(creator=command.auth.secret, secret=secret,
                            home=command.key,
                            roommate=command.command_type == CommandType.roommate)
            self._invites.upsert(invite)
            return invite
        else:  # find an invite object by secret code
            invite = self._invites.get(command.value)
            if invite is None:
                raise ArithmeticError('Invite not found. May be you are in the wrong interface?')
            home = self._homes.get(invite.home)
            if home is None:
                self._invites.delete_via_obj([invite])
                raise ArithmeticError(f'Home not found. May be it was deleted?')
            if invite.roommate:
                if command.auth.secret not in home.roommates:
                    home.roommates.append(command.auth.secret)
            else:
                if command.auth.secret not in home.invited:
                    home.invited.append(command.auth.secret)
            self._invites.delete_via_obj([invite])
            self._homes.upsert(home)
            owners = self._users.search('secret', invite.creator)
            if owners.__len__() > 0:
                test = [q for q in owners if q.interface == interface]
                if test.__len__() > 0:
                    owner_name = test.pop().name
                else:
                    owner_name = owners.pop().name
            else:
                owner_name = 'ERRNO'
            rooms = self._rooms.search('owner', home.owner)
            return {'owner': owner_name, 'rooms': [q.name for q in rooms],
                    'status': 'roommate' if invite.roommate else 'guest'}

    async def invite_clear(self, command, interface):
        invites = self._invites.search('creator', command.auth.secret)
        self._invites.delete_via_obj(invites)
        return True

    async def evict(self, command, interface):
        home = self._homes.get(command.auth.secret)
        if home is None:
            raise ArithmeticError(f'What')

        user = self._users.search('name', command.value)
        if user.__len__() > 1:
            user = [q for q in user if q.interface == interface]  # try finding the correct one
        if user.__len__() != 1:
            raise ArithmeticError(f'User {user} not found.')
        user = user[0]
        if user.secret in home.invited:
            home.invited.remove(user.secret)
        if user.secret in home.roommates:
            home.roommates.remove(user.secret)
        self._homes.upsert(home)
        return True

    async def shutdown(self, command, interface):
        print(f'Quartermaster shutting down')
        await self.__dispatch_to_all(Command(command_type=CommandType.shutdown))
        for q in Interface.impl_list():
            i = self.interfaces.get(q)
            if i is None:
                raise RuntimeError(f'Process for interface {q} not found')
            i['process'].join()
            self.__shutdown = True
        print('Quartermaster done full shutdown')

    async def save(self, command, interface):
        return True



    """ GENERAL COMMANDS SECTION END """

    def update_secrets(self, old_secret: pydantic.UUID5, new_secret: pydantic.UUID5):
        homes = self._homes.search_func(
            lambda o: old_secret in o.invited or old_secret in o.roommates
        )
        for home in homes:
            if old_secret in home.invited:
                home.invited.pop(home.invited.index(old_secret))
                home.invited.append(new_secret)
            if old_secret in home.roommates:
                home.roommates.pop(home.roommates.index(old_secret))
                home.roommates.append(new_secret)
            self._homes.upsert(home)
        rooms = self._rooms.search('owner', old_secret)
        for room in rooms:
            room.owner = new_secret
            self._rooms.upsert(room)

if __name__ == '__main__':
    q = QuarterMaster()
    q.run()