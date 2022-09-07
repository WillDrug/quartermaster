from src.interfaces.interface import Interface
import telebot
from time import sleep
from src.utility.command import CommandType, Command, Response
from src.model import Invite
from functools import wraps
from typing import Union
import re

"""
Private chat: 28 total
    * /secret * : set your secret to be an existing one or get your current one
    * /destroy : destroy your userinfo and all rooms (local happens on bot block (only for 1 interface))
    * /delroom name : deletes room by name (auto when bot is kicked from it)
    * !!! impossible, no way to remember ID /addroom name : adds a room for current interface
    * /editroom -> inline menu (WITH NAME if public chat, FAIL is public chat is not a room)
    * /editroom [name] -> inline menu (same or new message)
    * /editroom [name]: edits room. for name provided: name, invite link (address), closed, invited, without name: closed, invited
        * /editroom [name] [action] [value]
        * /locked [name]   by invite
        * /unlocked [name]
        * /closed [name]   fuck off
        * /open [name]
        * /name name [name] ->> prompt for name or change
        * /address name [link] ->> prompt for link or change
        * /kicktime name time
        * /roommate name user
        * /evict name user
    * /invite user room 
    * /uninvite [user\"all"] [room]
    
Public chat:
    * /locked
    * /unlocked
    * /closed
    * /open
    * /start -- makes a room out of this (if owner)
    * /name name 
    * /address link
    * /invite user
    * /delroom
    * /roommate user
    * /evict user
    
    
"""
def master_only(func):
    @wraps(func)
    def check_master(self, message):
        # AFTER AUTH (!)
        auth = self.userbase.get(message.from_user.id)
        if auth.key() not in self.config.master_ids:
            return
        return func(self, message)
    return check_master

def with_permission(func):
    @wraps(func)
    def check_permission(self, message):
        if self.is_public(message):
            user = message.from_user.id
            if isinstance(message, telebot.types.Message):
                chat_id = message.chat.id
            else:
                chat_id = message.message.chat.id
            if not chat_id == user and self.bot.get_chat_member(chat_id, user).status not in ['administrator',
                                                                                              'creator']:
                return
        return func(self, message)

    return check_permission


def with_auth(func):
    @wraps(func)
    def perform_auth(self, message):
        if message.from_user.id not in self.userbase:
            resp = self.auth(message.from_user.id, message.from_user.username)
            if not resp.error:
                self.userbase[message.from_user.id] = resp.data
            else:
                return self.bot.reply_to(message, f'There was an error while authenticating you: {resp.error_message}')
        return func(self, message)

    return perform_auth


class Telegram(Interface):
    def __init__(self, send_queue, receive_queue, *args, **kwargs):
        token = "1241832787:AAH2FDmx28_5KG7oKKuJrceKk8Hq38MD-iY"
        self.bot = telebot.TeleBot(token, parse_mode='HTML')
        self.prepare_bot()
        self.userbase = {}
        self.state = {}
        super().__init__(send_queue, receive_queue, *args, **kwargs)

    """ BOT SECTION """

    def prepare_bot(self):
        self.bot.message_handler(commands=['start', 'help'], func=self.is_private)(self.start_info_command)
        self.bot.message_handler(commands=['secret'], func=self.is_private)(self.secret_command)
        self.bot.message_handler(commands=['manage'], func=self.is_public)(self.manage_command)
        self.bot.message_handler(commands=['editroom'])(self.editroom_command)
        self.bot.inline_handler(func=lambda query: True)(self.inline)
        self.bot.callback_query_handler(lambda call: True)(self.editroom_command)
        self.handle = self.bot.get_me().username
        self.bot.message_handler(commands=['clearinvites'])(self.clearinvites_command)
        self.bot.message_handler(commands=['invite', 'roommate', 'evict'])(self.inviteroommateevict_command)
        # todo: make for-all-rooms commands change the defaults
        self.bot.message_handler(commands=['lock', 'unlock', 'close', 'open', 'timeout'])(
            self.lockunlockcloseopentimeout_command)
        self.bot.message_handler(commands=['destroy'])(self.destroy_command)
        self.bot.message_handler(commands=['save'])(self.save_command)
        self.bot.message_handler(commands=['shutdown'])(self.shutdown_command)
        # todo: send an invintation for an event for invited

    def inline(self, inline_query):
        r = telebot.types.InlineQueryResultArticle('1', 'Invite this user',
                                                   telebot.types.InputTextMessageContent(
                                                       f'https://t.me/{self.handle}?start={inline_query.query}'
                                                   ))
        self.bot.answer_inline_query(inline_query.id, [r], cache_time=1)

    def chat_type(self, message: Union[telebot.types.Message, telebot.types.CallbackQuery]):
        if isinstance(message, telebot.types.Message):
            chat_type = message.chat.type
        else:
            chat_type = message.message.chat.type
        return chat_type

    def is_public(self, message: Union[telebot.types.Message, telebot.types.CallbackQuery]):
        return self.chat_type(message) != 'private'

    def is_private(self, message: Union[telebot.types.Message, telebot.types.CallbackQuery]):
        return self.chat_type(message) == 'private'

    @with_auth
    def clearinvites_command(self, message):
        resp = self.clearinvites(self.userbase.get(message.from_user.id))
        if resp.error:
            txt = f'Failed to clear: {resp.error_message}'
        else:
            txt = 'Ok'
        self.bot.reply_to(message, txt)

    @with_auth
    def start_info_command(self, message):
        cmd = message.text.split()
        if cmd.__len__() > 1:
            resp = self.use_invite(self.userbase.get(message.from_user.id), cmd[1])
            if resp.error:
                self.bot.send_message(message.chat.id, f'Failed to process your invite: {resp.error_message}')
            else:
                txt = f'You have been invited by {resp.data["owner"].name} to join '
                txt += "their home" if resp.data["rooms"].__len__() == 0 else \
                    ("the following rooms: " + ", ".join(resp.data["rooms"]))
                txt += f' as a {resp.data["status"]}'
                self.bot.send_message(message.chat.id, txt)
        # todo: add deep linking secret creation
        rooms = self.get_own_rooms(self.userbase.get(message.from_user.id, None), None)
        if rooms.error:
            rooms = "Failed to fetch rooms: " + rooms.error_message
        elif rooms.data.__len__() == 0:
            rooms = ""
        else:
            rooms = '\n'.join(f"* {room.name} ({room.interface})" for room in rooms.data
                              if room.owner == self.userbase.get(message.chat.id).interface_id)
        self.bot.send_message(message.chat.id, f'You can manipulate your homes by name (invite link, locked\\unlocked, '
                                               f'open\\closed, timeout), invite people, set people as roommates and'
                                               f' stuff like that. Adding a home is done via a public channel.\n'
                                               f'To sync different interfaces, use /secret (channel secret)\n'
                                               f'Your rooms are:\n{rooms}'
                                               f'\n Auth is {self.userbase.get(message.from_user.id).secret}')

    @with_auth
    def secret_command(self, message):
        secret = message.text.split()
        secret = None if secret.__len__() == 1 else secret[1]
        if secret is None:
            secret = self.userbase.get(message.from_user.id).secret
            self.bot.send_message(message.chat.id, f'Your secret is: <pre>{secret.__str__()}</pre>')
        else:
            new_secret = self.merge_users(self.userbase.get(message.from_user.id), secret)
            rooms = self.get_own_rooms(self.userbase.get(message.from_user.id), None)
            if not new_secret.error:  # fixme move texts into interface.py
                text = f'Your accounts were merged, your new secret is now: {new_secret.data}'
            else:
                text = f'There was an error merging your accounts: {new_secret.error_message}'
            if rooms.error:
                text += f'\n Failed to get your rooms: {rooms.error_message}'
            elif rooms.data.__len__() > 0:
                text += '\n Your rooms are currently: ' + '\n'.join([q.name for q in rooms.data])
            self.bot.send_message(message.chat.id, text)

    @with_auth
    @with_permission
    def manage_command(self, message):
        if not self.bot.get_chat_member(message.chat.id, self.bot.get_me().id).status in ['administrator', 'creator']:
            return self.bot.reply_to(message, 'I don\'t have enough permissions to manage this group')
        # todo: may be disallow re-creation via /manage and force /delete or /destroy first
        invite = self.bot.get_chat(message.chat.id).invite_link or ''
        room = self.create_room(
            self.userbase.get(message.from_user.id),
            re.sub(r'[^0-9a-zA-Z]+', '', message.chat.title),
            invite,
            message.chat.id
        )  # create room with default params
        if room.error:
            return self.bot.send_message(message.chat.id, f'Failed to set this as a room: {room.error_message}')
        return self.bot.send_message(message.chat.id, f'Room was created with with the name "{room.data.name}"\n'
                                                      f'You can manage it from here or from local chat by name.')

    @with_auth
    @with_permission
    def editroom_command(self, message: Union[telebot.types.CallbackQuery, telebot.types.Message]):
        auth = self.userbase.get(message.from_user.id)
        if isinstance(message, telebot.types.Message):
            text = message.text
            chat_id = message.chat.id
            original_message = None
            callback_id = None
        else:
            text = message.data
            chat_id = message.message.chat.id
            original_message = message.message.id
            callback_id = message.id
        room = None
        command = None
        value = None
        cmd = text.split()
        if cmd.__len__() == 2 and cmd[1] == 'done':
            return self.editroom_menu(chat_id, 'Editing finished.', markup=None, original_message=original_message,
                                      callback_id=callback_id)
        private = self.is_private(message)
        rooms = self.get_own_rooms(self.userbase.get(message.from_user.id), None)  # fixme cache
        if rooms.error:
            return self.bot.send_message(message.chat.id, 'Failed to get rooms: ' + rooms.error_message)
        if not private:
            room = [q for q in rooms.data if q.interface_id == chat_id]
            if room.__len__() == 0:
                return self.bot.send_message(chat_id, 'This room is not managed')
            room = room.pop()
        if cmd.__len__() > 1:  # /editroom name or /editroom command
            if private:
                room = cmd[1]
            else:
                command = cmd[1]
        if cmd.__len__() > 2:  # /editroom name command or /editroom command value
            if private:  # re.sub
                command = cmd[2]
            else:
                value = cmd[2]
        if cmd.__len__() > 3:  # /editroom name command value or /editroom command value bullshit?
            if private:
                value = re.sub(r'[^0-9a-zA-Z]+', '', ''.join(cmd[3:]))
            else:
                value = re.sub(r'[^0-9a-zA-Z]+', '', ''.join(cmd[2:]))
        return self.editroom_recursive(auth, chat_id, rooms, room=room, command=command,
                                       value=value, original_message=original_message, callback_id=callback_id,
                                       public=self.is_public(message))

    def get_editroom_command(self, public, room=None, command=None, value=None):
        room_name = f' {room.name}' if not public else ''
        command = '' if command is None else f' {command}'
        value = '' if value is None else f' {value}'
        return f'/editroom{room_name}{command}{value}'

    def editroom_recursive(self, auth, chat_id, rooms, room=None, command=None, value=None,
                           original_message=None, callback_id=None, public=False):
        # if room is None -> present choices
        if command == 'done':
            return self.editroom_menu(chat_id, 'Editing finished', original_message=original_message,
                                      callback_id=callback_id)
        if room is None:
            markup = telebot.types.InlineKeyboardMarkup(row_width=4)  # fixme markup row()
            for room in rooms.data:
                markup.add(
                    telebot.types.InlineKeyboardButton(text=room.name, callback_data=f'/editroom {room.name}'))
            return self.editroom_menu(chat_id, 'Choose a room to edit', original_message=original_message,
                                      markup=markup, callback_id=callback_id)

        if isinstance(room, str):
            room_f = [q for q in rooms.data if q.name == room]
            if room_f.__len__() == 0:
                return self.bot.send_message(chat_id, f'{room} is not managed')
            room = room_f.pop()
        if command is None:
            markup = telebot.types.InlineKeyboardMarkup(row_width=4)
            if room.locked:
                markup.add(telebot.types.InlineKeyboardButton(text='Unlock',
                                                              callback_data=self.get_editroom_command(public, room,
                                                                                                      'unlock')))
            else:
                markup.add(telebot.types.InlineKeyboardButton(text='Lock',
                                                              callback_data=self.get_editroom_command(public, room,
                                                                                                      'lock')))
            if room.closed:
                markup.add(telebot.types.InlineKeyboardButton(text='Open',
                                                              callback_data=self.get_editroom_command(public, room,
                                                                                                      'open')))
            else:
                markup.add(telebot.types.InlineKeyboardButton(text='Close',
                                                              callback_data=self.get_editroom_command(public, room,
                                                                                                      'close')))

            markup.add(telebot.types.InlineKeyboardButton(text='Set Name',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'name')))
            markup.add(telebot.types.InlineKeyboardButton(text='Set Address',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'address')))
            markup.add(telebot.types.InlineKeyboardButton(text='Change Timeout',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'timeout')))
            if room.locked:
                markup.add(telebot.types.InlineKeyboardButton(text='Invite',
                                                              callback_data=self.get_editroom_command(public, room,
                                                                                                      'invite')))
            markup.add(telebot.types.InlineKeyboardButton(text='Add roommate',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'roommate')))
            markup.add(telebot.types.InlineKeyboardButton(text='Evict',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'evict')))
            markup.add(telebot.types.InlineKeyboardButton(text='DESTROY',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'destroy')))
            if not public:
                markup.add(telebot.types.InlineKeyboardButton(text='Back', callback_data=f'/editroom'))
            room_info = f"Name: {room.name}\nTimeout: {room.timeout}\nAddress: {room.address}\n"
            if room.locked:
                invited = self.users('secret', room.invited)
                if invited.error:
                    invited = f"Failed to get: {invited.error_message}"
                else:
                    invited = ', '.join([q.name for q in invited.data])
                room_info += f"Invited: {invited}\n"
            mates = self.users('secret', room.roommates)
            if mates.error:
                mates = f"Failed to get: {mates.error_message}"
            else:
                mates = ', '.join([q.name for q in mates.data])
            room_info += f"Roommates: {mates}"
            txt = f'Editing {room.name}\n{room_info}\nChoose an attribute to edit'
            return self.editroom_menu(chat_id, txt, markup=markup, original_message=original_message,
                                      callback_id=callback_id)

        if command == 'destroy':
            if value is None:
                self.bot.send_message(chat_id, 'Send me DESTROY to confirm')
                return self.add_prompt(chat_id, auth.interface_id, self.editroom_recursive, (auth, chat_id, rooms),
                                       {'command': command, 'room': room,
                                        'original_message': original_message, 'public': public,
                                        'callback_id': callback_id}, 'value')
            if value == 'DESTROY':
                self.destroy(auth, room=room.key())
                if public:
                    if original_message is not None:
                        self.editroom_recursive(auth, chat_id, rooms, room=room, command='done',
                                                original_message=original_message, callback_id=callback_id)
                    return self.bot.send_message(chat_id, 'Bye bye')
                else:
                    return self.editroom_recursive(auth, chat_id, rooms, original_message=original_message,
                                                   callback_id=callback_id)
            else:
                return self.bot.send_message(chat_id, 'good.')

        edit = {
            'lock': {'locked': True},
            'unlock': {'locked': False},
            'open': {'closed': False},
            'close': {'closed': True}
        }.get(command)
        if edit is None and command not in ['name', 'address', 'timeout', 'invite', 'roommate', 'evict']:
            return self.bot.send_message(chat_id, f'{command} is not something you can do with a room')

        if edit is not None:
            edit['key'] = room.key()
            resp = self.edit(auth, 'Room', edit)
            if resp.error:
                return self.bot.send_message(chat_id, f'Failed to edit the room: {resp.error_message}')
            if original_message is not None:
                return self.editroom_recursive(auth, chat_id, rooms, room=resp.data if public else resp.data.name,
                                               command=None, value=None, original_message=original_message,
                                               public=public)
            else:
                return self.editroom_menu(chat_id, 'Success', callback_id=callback_id)

        assert command in ['name', 'address', 'timeout', 'invite', 'roommate', 'evict']

        if value is None:  # requires a prompt
            if command == 'evict':
                guests = [q for q in room.invited + room.roommates]
                guests = self.users('secret', guests)
                if guests.error:
                    if callback_id is not None:
                        return self.bot.answer_callback_query(callback_id,
                                                              f"Failed to get users in rooms: {guests.error_message}")
                guests = guests.data
                markup = telebot.types.InlineKeyboardMarkup(row_width=3)
                for guest in guests:
                    markup.add(telebot.types.InlineKeyboardButton(text=guest.name,
                                                                  callback_data=self.get_editroom_command(
                                                                      public, room=room, command='evict',
                                                                      value=guest.name)
                                                                  )
                               )
                markup.add(telebot.types.InlineKeyboardButton(text='Back',
                                                              callback_data=self.get_editroom_command(public, room=room)
                                                              )
                           )
                return self.editroom_menu(chat_id, 'Choose a user to evict', markup=markup,
                                          original_message=original_message, callback_id=callback_id)
            self.add_prompt(chat_id, auth.interface_id, self.editroom_recursive, (auth, chat_id, rooms),
                            {'command': command, 'room': room, 'original_message': original_message,
                             'callback_id': callback_id,
                             'public': public}, 'value')
            pr = command if command in ['name', 'address', 'timeout'] else f'username to {command}'
            txt = f'Send me the {pr}'
            if command in ['invite', 'roommate'] and public:
                txt += '\n or better yet, use private chat.'
            self.bot.send_message(chat_id, txt)
        else:
            if command == 'timeout':
                try:
                    value = int(value)
                except ValueError:
                    return self.bot.send_message(chat_id, 'timeout should be a number of seconds')
            elif command == 'evict':  # todo: evict as inline keyboard. todo: inline mode invites.
                resp = self.evict(auth, value, room=room)
                if resp.error:
                    return self.bot.send_message(chat_id, f'Failed to evict: {resp.error_message}')
                self.bot.send_message(chat_id, f'{value} evicted')
                if callback_id is not None:
                    self.bot.answer_callback_query(callback_id, f'{value} evicted')
                    rooms = self.get_own_rooms(auth, None)
                    if rooms.error:
                        return self.bot.send_message(chat_id, f'Failed to get rooms: {rooms.error_message}')
                    rooms = rooms.data
                    room = [q for q in rooms if q.key() == room.key()]
                    if room.__len__() == 0:
                        return self.bot.send_message(chat_id, 'What the...')
                    room = room[0]
                    return self.editroom_recursive(auth, chat_id, rooms, room=room, command='evict',
                                                   value=None, original_message=original_message,
                                                   callback_id=callback_id, public=public)
                else:
                    return
            elif command in ['invite', 'roommate']:
                resp = self.invite(auth, value, rooms, room=room, can_use_invite=not public,
                                   roommate=command == 'roommate')
                if resp.error:
                    return self.bot.send_message(chat_id, f'Failed to {command}: {resp.error_message}')
                if isinstance(resp.data, Invite):
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton('Send invite', switch_inline_query=resp.data.secret))
                    return self.bot.send_message(chat_id, f'Use the send button to invite any user to '
                                                          f'{"" if room is None else f"to room {room.name}"}',
                                                 reply_markup=markup)
                if original_message is not None:
                    rooms = self.get_own_rooms(auth, None)
                    if rooms.error:
                        return self.bot.send_message(chat_id, 'Something went wrong tryign to update rooms:'
                                                     + rooms.error_message)
                    rooms = rooms.data
                    room = [q for q in rooms if q.key() == room.key()]
                    if room.__len__() == 0:
                        return self.bot.send_message(chat_id, 'Something went terribly wrong D:')
                    room = room[0]
                    if callback_id is not None:
                        self.bot.answer_callback_query(callback_id, f'{value} got {command}ed')
                    return self.editroom_recursive(auth, chat_id, rooms, room=room, command=None,
                                                   value=None, original_message=original_message,
                                                   callback_id=callback_id, public=public)
                return self.bot.send_message(chat_id, f'{value} got {command}ed!')

            edit = {
                'key': room.key(),
                command: value
            }
            r = self.edit(auth, 'Room', edit)
            if original_message is not None:
                if callback_id is not None:
                    self.bot.answer_callback_query(callback_id, f'Change ({command}) OK')
                rooms = self.get_own_rooms(auth, None)
                return self.editroom_recursive(auth, chat_id, rooms, room=r.data if public else r.data.name,
                                               command=None if command != 'evict' else 'evict', public=public,
                                               value=None, original_message=original_message)
            else:
                return self.editroom_menu(chat_id, 'Success', callback_id=callback_id)

    def editroom_menu(self, chat_id, text, markup=None, original_message=None, callback_id=None):
        if callback_id is not None:
            self.bot.answer_callback_query(callback_id, 'Ok')
        if markup is not None:
            markup.add(telebot.types.InlineKeyboardButton(text='Done', callback_data='/editroom done'))
        if original_message is None:
            self.bot.send_message(chat_id, text, reply_markup=markup)
        else:
            self.bot.edit_message_text(text, chat_id, original_message, reply_markup=markup)

    def add_prompt(self, chat_id, interface_id, func, args, kwargs, field):
        self.state[(str(chat_id), str(interface_id))] = (func, args, kwargs, field)
        self.bot.register_next_step_handler_by_chat_id(chat_id, self.prompt)

    @with_auth
    def prompt(self, message):
        try:
            func, args, kwargs, place = self.state[(str(message.chat.id), str(message.from_user.id))]
        except KeyError:
            return self.bot.reply_to(message, 'Error occured. I was waiting for something I do not know what')
        del self.state[(str(message.chat.id), str(message.from_user.id))]

        kwargs[place] = re.sub(r'[^0-9a-zA-Z]+', '', message.text)

        return func(*args, **kwargs)

    @with_auth
    @with_permission
    def inviteroommateevict_command(self, message):
        cmd = message.text.split()
        if cmd.__len__() == 1:
            value = None
        else:
            value = cmd[1]
        cmd = cmd[0][1:]
        if self.is_public(message):
            auth = self.userbase.get(message.from_user.id)
            rooms = self.get_own_rooms(self.userbase.get(message.from_user.id), None)  # fixme cache
            if rooms.error:
                return self.bot.send_message(message.chat.id, 'Failed to get rooms: ' + rooms.error_message)
            room = [q for q in rooms.data if q.interface_id == message.chat.id]
            if room.__len__() == 0:
                return self.bot.send_message(message.chat.id, 'This room is not managed')
            room = room[0]
            return self.editroom_recursive(auth, message.chat.id, rooms, room=room, command=cmd,
                                           value=value, original_message=None, public=True)
        return self.inviteroommateevict_command_recursive(message.chat.id, cmd, username=value)

    def inviteroommateevict_command_recursive(self, chat_id, command, username=None):
        if username is None:
            self.add_prompt(chat_id, chat_id, self.inviteroommateevict_command_recursive, (chat_id, command), {},
                            'username')
            return self.bot.send_message(chat_id, f'Send me a username to {command}')
        auth = self.userbase.get(chat_id)
        rooms = self.get_own_rooms(auth, None)
        if rooms.error:
            return self.bot.send_message(chat_id, f'Failed to fetch rooms: {rooms.error_message}')
        if rooms.data.__len__() == 0:
            return self.bot.send_message(chat_id, f'No rooms you own')
        invite = self.invite(auth, username, rooms.data, can_use_invite=True, roommate=command == 'roommate')
        if invite.error:
            return self.bot.send_message(chat_id, f'Failed to {command}: {invite.error_message}')
        if isinstance(invite.data, Invite):
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(telebot.types.InlineKeyboardButton('Send invite', switch_inline_query=invite.data.secret))
            return self.bot.send_message(chat_id, f'Use the send button to invite any user to your home',
                                         reply_markup=markup)
        return self.bot.send_message(chat_id, f'{username} {command}ed, ye.')

    def lockunlockcloseopentimeout_command(self, message):
        cmd = message.text.split()
        if cmd.__len__() == 1:
            value = None
        else:
            value = cmd[1]
        cmd = cmd[0][1:]
        rooms = self.get_own_rooms(self.userbase.get(message.from_user.id), None)  # fixme cache
        auth = self.userbase.get(message.from_user.id)
        if self.is_public(message):
            if rooms.error:
                return self.bot.send_message(message.chat.id, 'Failed to get rooms: ' + rooms.error_message)
            room = [q for q in rooms.data if q.interface_id == message.chat.id]
            if room.__len__() == 0:
                return self.bot.send_message(message.chat.id, 'This room is not managed')
            room = room[0]
            return self.editroom_recursive(auth, message.chat.id, rooms, room=room, command=cmd,
                                           value=value, original_message=None, public=True)
        else:
            key = cmd
            if cmd in ['lock', 'unlock']:
                key = 'locked'
            if cmd in ['open', 'close']:
                key = 'closed'
            if cmd in ['open', 'unlock']:
                value = False
            if cmd in ['close', 'lock']:
                value = True
            if key == 'timeout':
                try:
                    value = int(value)
                except ValueError:
                    return self.bot.send_message(message.chat.id, 'Timeout should be a number of seconds')
            e = [{'key': q.key(), key: value} for q in rooms.data]
            resp = self.edit(auth, 'Room', e)
            if resp.error:
                return self.bot.send_message(message.chat.id, f'Failed to {cmd}: {resp.error_message}')
            return self.bot.send_message(message.chat.id, f'Your home got {cmd}ed')

    def destroy_command(self, message):
        auth = self.userbase.get(message.from_user.id)
        rooms = self.get_own_rooms(auth, None)
        if rooms.error:
            return self.bot.send_message(message.chat.id, f'Failed to get rooms: {rooms.error_message}')
        if self.is_public(message):
            room = [q for q in rooms.data if q.interface_id == message.chat.id]
            if room.__len__() == 0:
                return self.bot.send_message(message.chat.id, 'This room is not managed')
            room = room[0]
            return self.editroom_recursive(auth, message.chat.id, rooms, room=room, command='destroy', public=True)
        cmd = message.text.split()
        if cmd.__len__() == 1:
            self.bot.send_message(message.chat.id, 'Send DESTROY to confirm.')
            return self.add_prompt(message.chat.id, message.chat.id, self.destroy_confirm,
                                   (auth, message.chat.id), {}, 'confirm')
        elif cmd.__len__() == 2:
            room = [q for q in rooms.data if q.name == cmd[1]]
            if room.__len__() == 0:
                return self.bot.send_message(message.chat.id, 'Not a room I know')
            return self.editroom_recursive(auth, message.chat.id, rooms, room=room, command='destroy',
                                           public=False)

    def destroy_confirm(self, auth, chat_id, confirm=''):
        if confirm == 'DESTROY':
            self.destroy(auth)
            return self.bot.send_message(chat_id, 'Bye bye')

    @with_auth
    @master_only
    def save_command(self, message):
        self.dispatch_command(Command(command_type=CommandType.save))

    @with_auth
    @master_only
    def shutdown_command(self, message):
        self.dispatch_command(Command(command_type=CommandType.shutdown), awaiting=False)

    """ BOT SECTION END """

    def initialize(self):
        while not self._shutdown:
            self.bot.polling()  # fixme configurable delay
            sleep(1)

    def local_shutdown(self):
        self.bot.stop_bot()


if __name__ == '__main__':
    from multiprocessing import Queue

    t = Telegram(Queue(), Queue())
    t.run()
