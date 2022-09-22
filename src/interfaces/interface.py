import asyncio
from abc import ABCMeta, abstractmethod
from time import sleep, time
import threading
from multiprocessing import Queue
from utility.command import CommandType, Command, Response
from model import *
from functools import wraps


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
        self.userbase = {}
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
            elif command.command_type == CommandType.merge:
                self.userbase[command.key] = command.value
                resp = None
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
        print(f'Interface {self.__class__} shutting down')
        self.local_shutdown()
        for t in self.threads:
            if t.is_alive():
                t.join()
        print(f'Interface {self.__class__} done full shutdown')

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

    def get_own_home(self, auth):
        return self.dispatch_command(Command(command_type=CommandType.homes, auth=auth))

    def _save_activity(self, user_id, username, room_id):
        if room_id not in self.activity:
            self.activity[room_id] = {}
        self.activity[room_id][user_id] = {'name': username, 'active': time.time()}

    def _user_joins(self, user_id, username, room_id):
        self._save_activity(user_id, username, room_id)
        return self.dispatch_command(Command(command_type=CommandType.join, key=room_id,
                                             value={'name': username, 'user_id': user_id}), awaiting=False)

    def _user_leaves(self, user_id, room_id):
        return self.dispatch_command(Command(command_type=CommandType.leave, key=room_id, value=user_id),
                                     awaiting=False)

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

    def add_guest(self, owner: Union[Home, User], guest: User, roommate=False):  # when two user objects exist
        if isinstance(owner, Home):
            if roommate:
                res = owner.roommates
            else:
                res = owner.invited
            if guest.secret not in res:
                res.append(guest.secret)
            resp = self.dispatch_command(Command(command_type=CommandType.edit, key='Home',
                                                 value={'key': owner.key(),
                                                        'roommates' if roommate else 'invited': res}))
            return resp
        else:
            invite = self.dispatch_command(Command(
                command_type=CommandType.roommate if roommate else CommandType.invite,
                auth=owner,
                key=owner.secret.__str__()
            ))
            if invite.error:
                return invite
            invite = invite.data
            return self.dispatch_command(Command(command_type=CommandType.invite, key=None, value=invite.secret,
                                                 auth=guest))

    def invite(self, auth, username, can_use_invite=False, roommate=False):
        home = self.get_own_home(auth)
        if home.error:
            return home
        home = home.data.pop()
        users = self.dispatch_command(Command(command_type=CommandType.users, key='name', value=username))
        if users.error:  # error'd
            return users
        else:
            users = users.data
        if users.__len__() > 1:
            users = [q for q in users if q.interface == self.__class__.__name__]  # username in THIS interface
        if users.__len__() != 1 and not can_use_invite:
            return Response(command_id=0, error=True, error_message='Did not manage to zero in on the user')
        elif users.__len__() != 1:
            invite = self.dispatch_command(Command(
                command_type=CommandType.roommate if roommate else CommandType.invite,
                auth=auth,
                key=home.key()
            ))
            return invite
        # got user, can add
        user = users.pop()
        return self.add_guest(home, user, roommate=roommate)

    def use_invite(self, auth, secret):
        return self.dispatch_command(Command(command_type=CommandType.invite, key=None, value=secret, auth=auth))

    def evict(self, auth, username):
        return self.dispatch_command(Command(command_type=CommandType.evict, value=username, auth=auth))

    def users(self, key, value):
        return self.dispatch_command(Command(command_type=CommandType.users, key=key, value=value))

    def clearinvites(self, auth):
        return self.dispatch_command(Command(command_type=CommandType.invite_clear, auth=auth))

    """ COMMON COMMANDS AND TEXT SECTION """

    def _secret_command(self, user_id, secret, callback):
        if secret is None:
            secret = self.userbase.get(user_id).secret
            text = f'Your secret is: <pre>{secret.__str__()}</pre>'
        else:
            new_secret = self.merge_users(self.userbase.get(user_id), secret)
            rooms = self.get_own_rooms(self.userbase.get(user_id), None)
            if not new_secret.error:  # fixme move texts into interface.py

                text = f'Your accounts were merged, your new secret is now: {new_secret.data}'
            else:
                text = f'There was an error merging your accounts: {new_secret.error_message}'
            if rooms.error:
                text += f'\n Failed to get your rooms: {rooms.error_message}'
            elif rooms.data.__len__() > 0:
                text += '\n Your rooms are currently: ' + '\n'.join([q.name for q in rooms.data])
        callback(text)

    def _manage_command(self, user, name, invite, chat_id) -> str:
        room = self.create_room(
            user,
            name,
            invite,
            chat_id
        )  # create room with default params
        if room.error:
            return f'There was an error trying to manage the room: {room.error_message}'
        else:
            return f'Room was created with {room.data.name} name!\nYou can manage it from here or from local chat.'

    def _edithome_recursive(self, auth, target, public, home, command=None, value=None):
        if command == 'done':
            return self.process_response(target, text='Editing done', reply_text='Done')
        if command is None:  # send main menu
            markup = []
            if home.locked:
                markup.append(('Unlock', '/edithome unlock'))
            else:
                markup.append(('Lock', '/edithome lock'))  # todo command prefix
            if home.closed:
                markup.append(('Open', '/edithome open'))
            else:
                markup.append(('Close', '/edithome close'))
            markup.append(('Set Timeout', '/edithome timeout'))
            markup.append(('Invite', '/edithome invite'))
            markup.append(('Add Roommate', '/edithome roommate'))
            markup.append(('Evict', '/edithome evict'))
            markup.append((f'Edit Room{"s" if public is None else ""}', '/edithome editroom'))
            markup.append((f'Done', '/edithome done'))
            txt = 'Editing your home:'
            if home.closed:
                txt += '\nIt is closed to all but rommates.'
            else:
                txt += '\nIt is open '
            if home.locked:
                txt += 'but locked for everyone except invited' if not home.closed else ''
            else:
                txt += '\nIt is unlocked (no invite necessary)'

            invited = self.users('secret', home.invited)
            roommates = self.users('secret', home.roommates)

            if invited.error:
                invited = invited.error_message
            else:
                invited = ', '.join([q.name for q in invited.data])
            if roommates.error:
                roommates = roommates.error_message
            else:
                roommates = ', '.join([q.name for q in roommates.data])
            txt += f'\nTimeout: {home.timeout} seconds'
            txt += f'\nInvited: {invited}'
            txt += f'\nRoommates: {roommates}'
            return self.process_response(target, text=txt, markup=markup)
        if command in ['lock', 'unlock', 'open', 'close']:  # do a thing, return
            edit = {
                'lock': {'locked': True},
                'unlock': {'locked': False},
                'open': {'closed': False},
                'close': {'closed': True}
            }.get(command)
            edit['key'] = home.key()
            resp = self.edit(auth, 'Home', edit)
            if resp.error:
                return self.process_response(target, reply_text=f'Failed to edit the room: {resp.error_message}')
            if self.is_callback(target):
                return self._edithome_recursive(auth, target, public, resp.data)
            else:
                return self.process_response(target, 'Edited!')
        if command == 'editroom':  # send another menu
            rooms = self.get_own_rooms(auth, None)
            if rooms.error:
                return self.process_response(target, reply_text=f'Failed to get rooms: {rooms.error_message}')
            if public is not None:
                room = [q for q in rooms.data if q.interface_id == public]  # fixme there's no chat id in common :(
                if room.__len__() == 0:
                    return self.process_response(target, reply_text='This room is not managed, use private.')
                room = room.pop()
            else:
                room = None
            return self._editroom_recursive(auth, target, rooms.data, public, room=room)
        if command == 'timeout':  # prompt for a number or set
            if value is None:
                return self._add_prompt('New Timeout Value', target, self._edithome_recursive,
                                        (auth, target, public, home),
                                        {'command': command}, 'value')
            else:
                try:
                    value = int(value)
                except ValueError:
                    return self.process_response(target, reply_text='Timeout should be a number of seconds')
                resp = self.edit(auth, "Home", {"key": home.key(), "timeout": value})
                if resp.error:
                    return self.process_response(target, reply_text=f'Failed to set timeout: {resp.error_message}')
                if not self.is_callback(target):
                    return self.process_response(target, reply_text='Timeout is set.')
                return self._edithome_recursive(auth, target, public, resp.data)
        if command in ['invite', 'roommate']:  # prompt for a user, send or give invite
            if value is not None:
                invite = self.invite(auth, value, can_use_invite=public is None, roommate=command == 'roommate')
                if invite.error:
                    return self.process_response(target, reply_text=f'Failed: {invite.error_message}')
                if isinstance(invite.data, Invite):
                    txt = f'Send this as an invite: ' + self._get_deep_link(invite.data.secret.__str__())
                    return self.process_response(target, text=txt, reply_text='Invite created')  # todo markup?
                else:
                    return self.process_response(target, reply_text='User invited')
            else:
                return self._add_prompt('username', target, self._edithome_recursive,
                                        (auth, target, public, home), {'command': command}, 'value')

        if command == 'evict':  # send another menu
            if value is None:  # todo: make evict from roommates or invited specifically possible
                secrets = home.roommates + home.invited
                users = self.users('secret', secrets)
                if users.error:
                    return self.process_response(target, reply_text=f'Failed to fetch users: {users.error_message}')
                # clean usernames to this interface if exist:
                d = {}
                for user in users.data:
                    if user.secret.__str__() not in d:
                        d[user.secret.__str__()] = user
                    if user.interface == self.__class__.__name__:
                        d[user.secret.__str__()] = user
                users = [d[k] for k in d]
                markup = []
                for user in users:
                    markup.append((user.name, f'/edithome evict {user.name}'))
                markup.append(('Back', '/edithome'))
                markup.append(('Done', '/edithome done'))
                return self.process_response(target, text='Choose a user to evict', markup=markup)
            else:
                resp = self.evict(auth, value)
                if resp.error:
                    return self.process_response(target, reply_text=f'Failed to evict: {resp.error_message}')
                home = self.get_own_home(auth)
                if home.error:
                    return self.process_response(target, reply_text=f'User was evicted but I failed to fetch your home:'
                                                                    f' {home.error_message}')
                else:
                    try:
                        home = home.data.pop()
                    except IndexError:
                        return self.process_response(target, reply_text=f'User was evicted but I failed to '
                                                                        f'fetch your home: no home found?')
                if self.is_callback(target):
                    return self._edithome_recursive(auth, target, public, home, command=command)
                else:
                    return self.process_response(target, reply_text='Evicted.')
        return self.process_response(target, text='Unkown Command')

    def get_editroom_command(self, public, room=None, command=None, value=None):
        room_name = f' {room.name}' if not public else ''
        command = '' if command is None else f' {command}'
        value = '' if value is None else f' {value}'
        return f'/editroom{room_name}{command}{value}'

    def _editroom_recursive(self, auth, target, rooms, public: bool, room=None, command=None, value=None):
        if command == 'done':
            return self.process_response(target, text='Editing finished')
        if room is None and not public:
            markup = []
            for room in rooms:
                markup.append((room.name, f'/editroom {room.name}'))
            markup.append(('Edit Home', '/edithome'))
            markup.append(('Done', '/editroom done'))
            return self.process_response(target, text='Choose a room to edit\n To add new one, '
                                                      'add the bot to a channel as an admin an run /manage',
                                         markup=markup)  # todo prefix
        if command is None:
            markup = []
            markup.append(('Set Name', self.get_editroom_command(public, room=room, command='name')))
            markup.append(('Set Address', self.get_editroom_command(public, room=room, command='address')))
            markup.append(('DESTROY', self.get_editroom_command(public, room=room, command='destroy')))
            if not public:
                markup.append(('Choose Room', '/editroom'))
            else:
                markup.append(('Edit Home', '/edithome'))
            markup.append(('Done', '/editroom done'))
            room_info = f"Name: {room.name}\nAddress: {room.address}\nChoose an edit:"
            return self.process_response(target, text=room_info, markup=markup)
        if command == 'destroy':
            if value is None:
                return self._add_prompt('DESTROY to confirm', target, self._editroom_recursive,
                                        (auth, target, rooms, public),
                                        {'command': command, 'room': room}, 'value')
            if value == 'DESTROY':
                self.destroy(auth, room=room.key())
                if not public:
                    return self._editroom_recursive(auth, target, rooms, public)
                return self.process_response(target, text='Bye bye', reply_text='Room not managed now')
            else:
                return self.process_response(target, reply_text='Good.')
        if command not in ['name', 'address']:
            return self.process_response(target, reply_text='Unknown command')
        if value is None:
            txt = f'New {command} ("*" to autoset)'
            self._add_prompt(txt, target, self._editroom_recursive, (auth, target, rooms, public),
                             {'command': command, 'room': room}, 'value')
        else:
            if value == '*':
                if command == 'address':  # todo different interface room edit ?
                    value = self._get_address(target)
                if command == 'name':
                    value = self._get_name(target)
            edit = {
                'key': room.key(),
                command: value
            }
            r = self.edit(auth, 'Room', edit)
            if r.error:
                return self.process_response(target, reply_text=f'Failed to update: {r.error_message}')
            if self.is_callback(target):
                return self._editroom_recursive(auth, target, rooms, public, room=r.data)
            else:
                return self.process_response(target, reply_text='Success')

    @abstractmethod
    def _get_deep_link(self, extra):
        pass

    @abstractmethod
    def _get_address(self, target):
        pass

    @abstractmethod
    def _get_name(self, target):
        pass

    @abstractmethod
    def _add_prompt(self, help_text, target, func, args, kwargs, field_name):
        pass

    @abstractmethod
    def process_response(self, target, text=None, reply_text=None, markup=None):
        """

        :param target:
        :param text: text of the main message
        :param reply_text: ephemeral response OR sent message
        :param markup: buttons (list of tuples)
        :return:
        """
        pass

    @abstractmethod
    def is_callback(self, target):
        pass
