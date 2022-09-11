import asyncio
import time
import uuid

from interfaces.interface import Interface
import telebot
from time import sleep
from utility.command import CommandType, Command, Response
from model import Invite
from functools import wraps
from typing import Union
import re
# todo add discord
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
def get_member(bot, chat_id, user_id):
    chat = bot.get_chat(chat_id)
    member = bot.get_chat_member(chat_id, user_id)
    # AnonymousGroupBot or Telegram user for channel posts
    if chat.type == 'supergroup' and member.user.id in [1087968824, 777000]:
        admins = bot.get_chat_administrators(chat.id)
        admins = [q for q in admins if q.status=='creator']
        if admins.__len__() == 0:
            return None
        member = admins.pop()
    return member


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
            member = get_member(self.bot, chat_id, message.from_user.id)
            if chat_id != user and member.status not in ['administrator', 'creator']:  #
                return
        return func(self, message)

    return check_permission


def with_auth(func):
    @wraps(func)
    def perform_auth(self, message):
        if isinstance(message, telebot.types.CallbackQuery):
            chat_id = message.message.chat.id
        else:
            chat_id = message.chat.id
        member = get_member(self.bot, chat_id, message.from_user.id)
        if member is None:
            return
        if member.user != message.from_user:
            message.from_user = member.user
        if member.user.id not in self.userbase: # and not member.user.is_bot:
            if message.from_user.username is not None:
                username = message.from_user.username
            else:
                first = message.from_user.first_name
                last = message.from_user.last_name
                if first is None:
                    first = ""
                if last is None:
                    last = ""
                first = re.sub(r'[^0-9a-zA-Zа-яА-Я]+', '', first)
                last =  re.sub(r'[^0-9a-zA-Zа-яА-Я]+', '', last)
                username = first+last+member.user.id.__str__()[-5:]
            resp = self.auth(message.from_user.id, username)

            if not resp.error:
                self.userbase[message.from_user.id] = resp.data
            else:
                return self.bot.reply_to(message, f'There was an error while authenticating you: {resp.error_message}')
        return func(self, message)

    return perform_auth


class Telegram(Interface):
    def __init__(self, send_queue, receive_queue, config, *args, **kwargs):
        token = config.telegram_auth
        self.bot = telebot.TeleBot(token, parse_mode='HTML')
        self.handle = ""
        self.prepare_bot()
        self.userbase = {}
        self.state = {}
        super().__init__(send_queue, receive_queue, config, *args, **kwargs)

    """ BOT SECTION """
    def prepare_bot(self):
        self.bot.message_handler(commands=['start'], func=self.is_private)(self.start_command)
        self.bot.message_handler(commands=['secret'], func=self.is_private)(self.secret_command)
        self.bot.message_handler(commands=['manage'], func=self.is_public)(self.manage_command)
        self.bot.message_handler(commands=['editroom'])(self.editroom_command)
        self.bot.message_handler(commands=['edithome'])(self.edithome_command)
        self.bot.inline_handler(func=lambda query: True)(self.inline)
        self.bot.callback_query_handler(lambda call: call.data.startswith('/editroom'))(self.editroom_command)
        self.bot.callback_query_handler(lambda call: call.data.startswith('/edithome'))(self.edithome_command)
        self.handle = self.bot.get_me().username
        self.bot.message_handler(commands=['clearinvites'])(self.clearinvites_command)
        self.bot.message_handler(commands=[
            'invite', 'roommate', 'evict', 'lock', 'unlock', 'close', 'open', 'timeout'
        ])(self.edithome_single_command)
        self.bot.message_handler(commands=['name', 'address'])(self.editroom_single_command)
        self.bot.message_handler(commands=['destroy'])(self.destroy_command)
        self.bot.message_handler(commands=['save'])(self.save_command)
        self.bot.message_handler(commands=['shutdown'])(self.shutdown_command)
        # todo: send an invitation for an event for invited
        self.bot.message_handler(func=lambda message: not message.text.startswith('/') and
                                                      self.is_public(message))(self.save_activity)
        # todo: check on join if invited and shit
        self.bot.chat_member_handler()(self.chat_member_event)
        self.bot.message_handler(commands=['info'])(self.info)
        self.bot.callback_query_handler(lambda call: call.data.startswith('/rooms'))(self.show_home_rooms)
        self.bot.message_handler(commands=['rooms'])(self.show_home_rooms)
        self.bot.callback_query_handler(lambda call: call.data.startswith('/homes'))(self.show_homes)
        self.bot.message_handler(commands=['homes'], func=self.is_private)(self.show_homes)

    def info(self, message):
        if self.is_public(message):
            return self.show_home_rooms(message)
        else:
            return self.show_homes(message)

    @with_auth
    def show_home_rooms(self, message):
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
        user_id = message.from_user.id
        auth = self.userbase.get(user_id)
        if text.split().__len__() > 1 and text.split()[1] == 'done':
            return self.inline_menu(chat_id, original_message=original_message, text='Inline menu closed',
                             callback_text='Done', callback_id=callback_id, command='/rooms')
        if self.is_public(message):
            home = self.get_current_home(auth, chat_id)
        else:
            cmd = text.split()
            if cmd.__len__() == 1:
                return self.bot.send_message(chat_id, f'Provide a username of a home owner.')
            username = cmd[1]
            try:
                secret = uuid.UUID(username)
                home = self.get_home(auth, secret)
            except ValueError:
                users = self.users('name', username)
                if users.error or users.data.__len__() == 0:
                    return self.bot.send_message(chat_id, 'Did not find this user')
                home = users.data.pop().secret
                home = self.get_home(auth, home)

        if home.error:
            return self.bot.send_message(chat_id, f'Failed to get the home: {home.error_message}')

        if isinstance(home.data, list):
            try:
                home.data = home.data[0]
            except IndexError:
                return self.bot.send_message(chat_id, 'Home not found?')
        rooms = self.get_home_rooms(auth, home.data.key())
        if rooms.error:
            return self.bot.send_message(chat_id, f'Failed to get rooms: {rooms.error_message}')
        if self.is_public(message):
            return self.bot.send_message(chat_id,
                                         "The rooms are:\n" +
                                         "\n".join(f'<a href="{room.address}">{room.name}</a>' for room in rooms.data))
        else:
            markup = telebot.types.InlineKeyboardMarkup()
            for room in rooms.data:
                markup.add(telebot.types.InlineKeyboardButton(room.name, url=room.address))
            markup.add(telebot.types.InlineKeyboardButton('Back to homes', callback_data='/homes'))
            return self.inline_menu(chat_id, text='Here are the rooms', markup=markup,
                                    original_message=original_message, callback_id=callback_id, command='/homes')

    @with_auth
    def show_homes(self, message):
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
        if text.split().__len__() > 1 and text.split()[1] == 'done':
            return self.inline_menu(chat_id, original_message=original_message, text='Inline menu closed',
                             callback_text='Done', callback_id=callback_id, command='/homes')
        homes = self.get_visible_homes(self.userbase.get(message.from_user.id))
        if homes.error:
            return self.bot.send_message(chat_id, f'Failed to get homes: {homes.error_message}')
        if homes.data.__len__() > 20:
            homes = homes.data[:20]
        else:
            homes = homes.data
        users = self.users('secret', [q.owner for q in homes])
        if users.error:
            return self.bot.send_message(chat_id, 'Failed to find usernames')
        markup = telebot.types.InlineKeyboardMarkup()
        for user in users.data:
            markup.add(
                telebot.types.InlineKeyboardButton(f'{user.name}\'s Home', callback_data=f'/rooms {user.secret}'))
        return self.inline_menu(chat_id, text='Homes available:', markup=markup, original_message=original_message,
                                callback_id=callback_id, callback_text='Fetched homes', command='/homes')

    @with_auth
    def chat_member_event(self, event: telebot.types.ChatMemberUpdated):
        if event.old_chat_member.status == 'left' and event.new_chat_member.status != 'left':
            self._user_joins(event.new_chat_member.user.id, event.new_chat_member.user.username, event.chat.id)
        if event.old_chat_member.status != 'left' and event.new_chat_member.status == 'left':
            self._user_leaves(event.new_chat_member.user.id, event.chat.id)
        self.sync()

    @with_auth
    def save_activity(self, message: telebot.types.Message):  # todo cache managed rooms
        self._save_activity(message.from_user.id, message.from_user.username, message.chat.id)

    def inline(self, inline_query):
        res = [telebot.types.InlineQueryResultArticle('1', 'Invite via code',
                                                      telebot.types.InputTextMessageContent(
                                                          f'https://t.me/{self.handle}?start={inline_query.query}'
                                                      ))]
        auth = self.auth(inline_query.from_user.id, inline_query.from_user.username)
        if not auth.error:
            rooms = self.get_own_rooms(auth.data, None)
            if not rooms.error:
                rooms = rooms.data
                for room in rooms:
                    res.append(telebot.types.InlineQueryResultArticle(room.name, f'Give {room.name} address',
                                                                      telebot.types.InputTextMessageContent(
                                                                          f'You can join {room.name} via {room.address}'
                                                                      )))

        self.bot.answer_inline_query(inline_query.id, res, cache_time=1)

    def chat_type(self,
                  message: Union[telebot.types.Message, telebot.types.CallbackQuery, telebot.types.ChatJoinRequest]):
        if isinstance(message, telebot.types.Message):
            chat_type = message.chat.type
        elif isinstance(message, telebot.types.ChatJoinRequest):
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
    def start_command(self, message):
        cmd = message.text.split()
        if cmd.__len__() > 1:
            resp = self.use_invite(self.userbase.get(message.from_user.id), cmd[1])
            if resp.error:
                self.bot.send_message(message.chat.id, f'Failed to process your invite: {resp.error_message}')
            else:
                txt = f'You have been invited by {resp.data["owner"]} to join their home as a {resp.data["status"]}'
                txt += f"You can get the rooms by using <pre>/rooms {resp.data['owner']}</pre>"
                return self.bot.send_message(message.chat.id, txt)
        # todo: add deep linking secret creation
        rooms = self.get_own_rooms(self.userbase.get(message.from_user.id, None), None)
        if rooms.error:
            rooms = "Failed to fetch rooms: " + rooms.error_message
        elif rooms.data.__len__() == 0:
            rooms = ""
        else:
            rooms = '\n'.join(f"* {room.name} ({room.interface})" for room in rooms.data)
        home = self.get_own_home(self.userbase.get(message.from_user.id))
        if home.error:
            return self.bot.send_message(message.chat.id, f'failed to fetch home: {home.error_message}')
        home = home.data.pop()
        self.bot.send_message(message.chat.id,
                              f'Your username in this bot is: {self.userbase.get(message.from_user.id).name}'
                              f'Your home is set up. It is {"closed" if home.closed else "open"}\n'
                              f'It is also {"locked" if home.locked else "unlocked"}.\n'
                              f'Locking the home will require an invite within the bot.\n'
                              f'Closed home only allows roommates to be there.\n'
                              f'Timeout is {home.timeout}\n'
                              f'To see who you invited you can use /edithome command.\n'
                              f'Your rooms are: \n {rooms}')

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
    def edithome_command(self, message: Union[telebot.types.CallbackQuery, telebot.types.Message]):
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
        cmd = text.split()
        command = None
        value = None
        if cmd.__len__() > 1:
            command = cmd[1]
        if cmd.__len__() > 2:
            value = cmd[2]
        home = self.get_own_home(auth)
        if home.error:
            return self.bot.reply_to(message, f'Failed to get your home: {home.error_message}')
        home = home.data.pop()
        return self.edithome_recursive(auth, chat_id, self.is_public(message), home, command=command, value=value,
                                       original_message=original_message, callback_id=callback_id)

    def edithome_recursive(self, auth, chat_id, public, home, command=None, value=None, original_message=None,
                           callback_id=None, callback_text=None):
        # todo: add sync to /address
        if command == 'done':
            return self.inline_menu(chat_id, text='Inline menu closed.', original_message=original_message,
                                    callback_id=callback_id, callback_text='Editing finished', command='/edithome')
        if command is None:
            markup = telebot.types.InlineKeyboardMarkup(row_width=4)
            if home.locked:
                markup.add(telebot.types.InlineKeyboardButton(text='Unlock',
                                                              callback_data='/edithome unlock'))
            else:
                markup.add(telebot.types.InlineKeyboardButton(text='Lock',
                                                              callback_data='/edithome lock'))
            if home.closed:
                markup.add(telebot.types.InlineKeyboardButton(text='Open',
                                                              callback_data='/edithome open'))
            else:
                markup.add(telebot.types.InlineKeyboardButton(text='Close',
                                                              callback_data='/edithome close'))

            markup.add(telebot.types.InlineKeyboardButton(text='Set Timeout',
                                                          callback_data='/edithome timeout'))
            markup.add(telebot.types.InlineKeyboardButton(text='Invite',
                                                          callback_data='/edithome invite'))
            markup.add(telebot.types.InlineKeyboardButton(text='Add roommate',
                                                          callback_data='/edithome roommate'))
            markup.add(telebot.types.InlineKeyboardButton(text='Evict',
                                                          callback_data='/edithome evict'))
            markup.add(telebot.types.InlineKeyboardButton(text=f'Edit Room{"s" if not public else ""}',
                                                          callback_data='/edithome editroom'))
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
            return self.inline_menu(chat_id, text=txt, markup=markup, original_message=original_message,
                                    callback_id=callback_id, callback_text=callback_text, command='/edithome')
        # command can be /lock /unlock /close /open /timeout /invite /roommate /evict -- done via /edithome or /single
        # for rooms leave /name /address /destroy and the same from /editroom (menu)
        if command in ['lock', 'unlock', 'open', 'close']:
            edit = {
                'lock': {'locked': True},
                'unlock': {'locked': False},
                'open': {'closed': False},
                'close': {'closed': True}
            }.get(command)
            edit['key'] = home.key()
            resp = self.edit(auth, 'Home', edit)
            if resp.error:
                return self.bot.send_message(chat_id, f'Failed to edit the room: {resp.error_message}')
            if original_message:
                return self.edithome_recursive(auth, chat_id, public, resp.data, original_message=original_message,
                                               callback_id=callback_id, callback_text=f'Home: {command} OK')
            else:
                return self.bot.send_message(chat_id, 'Done')
        if command == 'editroom':
            rooms = self.get_own_rooms(auth, None)
            if rooms.error:
                return self.bot.send_message(chat_id, f'Failed to get rooms: {rooms.error_message}')
            if public:
                room = [q for q in rooms.data if q.interface_id == chat_id]
                if room.__len__() == 0:
                    return self.bot.send_message(chat_id, 'This room is not managed, use private.')
                room = room.pop()
            else:
                room = None
            return self.editroom_recursive(auth, chat_id, rooms.data, room=room, original_message=original_message,
                                           callback_id=callback_id, public=public)
        if command == 'timeout':
            if value is None:
                self.add_prompt(chat_id, auth.interface_id, self.edithome_recursive, (auth, chat_id, public, home),
                                {'original_message': original_message, 'callback_id': callback_id,
                                 'command': command}, 'value')
                return self.bot.send_message(chat_id, f'Send me the new timeout')
            else:
                try:
                    value = int(value)
                except ValueError:
                    if callback_id:
                        self.bot.answer_callback_query(callback_id, 'Timeout failed to set')
                    return self.bot.send_message(chat_id, 'timeout should be a number of seconds')
                resp = self.edit(auth, "Home", {"key": home.key(), "timeout": value})
                if resp.error:
                    return self.bot.send_message(chat_id, f'failed to set timeout: {resp.error_message}')
                return self.edithome_recursive(auth, chat_id, public, resp.data, original_message=original_message,
                                               callback_id=callback_id, callback_text='Timeout is set')
        if command in ['invite', 'roommate']:
            if value is not None:
                invite = self.invite(auth, value, can_use_invite=not public, roommate=command == 'roommate')
                if invite.error:
                    return self.bot.send_message(chat_id, f'Failed: {invite.error_message}')
                if isinstance(invite.data, Invite):
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton('Send an invite',
                                                                  switch_inline_query=invite.data.secret.__str__()))
                    return self.bot.send_message(chat_id, f'Invite created which you can use. '
                                                          f'Or give this link: https://t.me/'
                                                          f'{self.handle}?start={invite.data.secret.__str__()}',
                                                 reply_markup=markup)
                else:
                    if original_message is not None:
                        return self.edithome_recursive(auth, chat_id, public, home, original_message=original_message,
                                                       callback_id=callback_id, callback_text='User invited.')
                    else:
                        return self.bot.send_message(chat_id, 'Invited.')
            else:
                self.add_prompt(chat_id, auth.interface_id, self.edithome_recursive, (auth, chat_id, public, home),
                                {'original_message': original_message, 'callback_id': callback_id,
                                 'command': command}, 'value')
                return self.bot.send_message(chat_id,
                                             f'Send me a username. {"Use private chat to get a sendable invite" if public else ""}')

        if command == 'evict':
            if value is None:  # todo: make evict from roommates or invited specifically possible
                secrets = home.roommates + home.invited
                users = self.users('secret', secrets)
                if users.error:
                    return self.bot.send_message(chat_id, f'Failed to fetch users: {users.error_message}')
                # clean usernames to this interface if exist:
                d = {}
                for user in users.data:
                    if user.secret.__str__() not in d:
                        d[user.secret.__str__()] = user
                    if user.interface == self.__class__.__name__:
                        d[user.secret.__str__()] = user
                users = [d[k] for k in d]
                markup = telebot.types.InlineKeyboardMarkup(row_width=2)
                for user in users:
                    markup.add(
                        telebot.types.InlineKeyboardButton(user.name, callback_data=f'/edithome evict {user.name}'))
                markup.add(telebot.types.InlineKeyboardButton('Back', callback_data=f'/edithome'))
                return self.inline_menu(chat_id, text='Choose a user to evict', markup=markup,
                                        original_message=original_message,
                                        callback_id=callback_id, callback_text=callback_text, command='/edithome')
            else:
                resp = self.evict(auth, value)
                if resp.error:
                    self.bot.send_message(chat_id, f'Failed to evict: {resp.error_message}')
                home = self.get_own_home(auth)
                if home.error:
                    return self.bot.send_message(
                        f'User was evicted but I failed to fetch your home: {home.error_message}')
                else:
                    home = home.data
                if original_message is not None:
                    return self.edithome_recursive(auth, chat_id, public, home, command=command,
                                                   original_message=original_message, callback_id=callback_id,
                                                   callback_text='User evicted')
                else:
                    return self.bot.send_message(chat_id, 'Evicted.')

        return self.bot.send_message(chat_id, 'Command unknown')

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
            return self.inline_menu(chat_id, 'Inline menu closed.', markup=None, original_message=original_message,
                                    callback_id=callback_id)
        private = self.is_private(message)
        rooms = self.get_own_rooms(self.userbase.get(message.from_user.id), None)  # fixme cache
        if rooms.error:
            return self.bot.send_message(message.chat.id, 'Failed to get rooms: ' + rooms.error_message)
        rooms = rooms.data
        if not private:
            room = [q for q in rooms if q.interface_id == chat_id]
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
            return self.inline_menu(chat_id, 'Editing finished', original_message=original_message,
                                    callback_id=callback_id)
        if room is None:
            markup = telebot.types.InlineKeyboardMarkup(row_width=4)  # fixme markup row()
            for room in rooms:
                markup.add(
                    telebot.types.InlineKeyboardButton(text=room.name, callback_data=f'/editroom {room.name}'))
            markup.add(
                telebot.types.InlineKeyboardButton(text='Edit Home', callback_data=f'/edithome')
            )
            return self.inline_menu(chat_id, 'Choose a room to edit\n To add new one, '
                                             'add the bot to a channel as an admin and run /manage',
                                    original_message=original_message, markup=markup, callback_id=callback_id)

        if isinstance(room, str):
            room_f = [q for q in rooms if q.name == room]
            if room_f.__len__() == 0:
                return self.bot.send_message(chat_id, f'{room} is not managed')
            room = room_f.pop()
        if command is None:
            markup = telebot.types.InlineKeyboardMarkup(row_width=4)
            markup.add(telebot.types.InlineKeyboardButton(text='Set Name',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'name')))
            markup.add(telebot.types.InlineKeyboardButton(text='Set Address',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'address')))
            markup.add(telebot.types.InlineKeyboardButton(text='DESTROY',
                                                          callback_data=self.get_editroom_command(public, room,
                                                                                                  'destroy')))
            if not public:
                markup.add(telebot.types.InlineKeyboardButton(text='Choose Room', callback_data=f'/editroom'))
            else:
                markup.add(telebot.types.InlineKeyboardButton(text='Edit Home', callback_data=f'/edithome'))
            room_info = f"Name: {room.name}\nAddress: {room.address}\nChoose an edit"

            return self.inline_menu(chat_id, room_info, markup=markup, original_message=original_message,
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

        assert command in ['name', 'address']

        if value is None:  # requires a prompt
            self.add_prompt(chat_id, auth.interface_id, self.editroom_recursive, (auth, chat_id, rooms),
                            {'command': command, 'room': room, 'original_message': original_message,
                             'callback_id': callback_id, 'public': public}, 'value')
            txt = f'Send me the new {command}'
            if command == 'address':
                txt += '; Use * to fetch the address from the interface'
            self.inline_menu(chat_id, text=txt, callback_id=callback_id, callback_text='Ready for the value.')
        else:
            if command == 'address' and value == '*':
                value = self.bot.get_chat(chat_id).invite_link
            edit = {
                'key': room.key(),
                command: value
            }
            r = self.edit(auth, 'Room', edit)
            if r.error:
                return self.bot.send_message(chat_id, f'Failed to update: {r.error_message}')
            if original_message is not None:
                return self.editroom_recursive(auth, chat_id, rooms, room=r.data,
                                               command=None, public=public, value=None,
                                               original_message=original_message, callback_id=callback_id)
            else:
                return self.inline_menu(chat_id, 'Success')

    def inline_menu(self, chat_id, text=None, markup=None, original_message=None, callback_id=None, callback_text='Ok',
                    command='/editroom'):
        if callback_id is not None:
            self.bot.answer_callback_query(callback_id, callback_text)
        if markup is not None:
            markup.add(telebot.types.InlineKeyboardButton(text='Done', callback_data=f'{command} done'))
        if original_message is None and text is not None:
            self.bot.send_message(chat_id, text, reply_markup=markup)
        elif text is not None:
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

        kwargs[place] = re.sub(r'[^0-9a-zA-Z\*]+', '', message.text)

        return func(*args, **kwargs)

    @with_auth
    def edithome_single_command(self, message):
        auth = self.userbase.get(message.from_user.id)
        home = self.get_own_home(auth)
        if home.error:
            return self.bot.reply_to(message, f'Failed to find your home: {home.error_message}')
        else:
            home = home.data.pop()
        cmd = message.text.split()
        if cmd.__len__() == 1:
            value = None
        else:
            value = cmd[1]
        cmd = cmd[0][1:]
        return self.edithome_recursive(auth, message.chat.id, self.is_public(message), home, command=cmd, value=value)

    @with_auth
    @with_permission
    def editroom_single_command(self, message):
        auth = self.userbase.get(message.from_user.id)
        rooms = self.get_own_rooms(auth, None)
        if rooms.error:
            return self.bot.send_message(message.chat.id, f'Failed to get rooms: {rooms.error_message}')
        room = None
        cmd = message.text.split()
        command = cmd[0][1:]
        if self.is_private(message):
            if cmd.__len__() == 1:
                return self.bot.send_message(message.chat.id, 'Please specify a room name')
            room = cmd[1]
            value = None
            if cmd.__len__() > 2:
                value = cmd[2]
        else:
            room = [q for q in rooms.data if q.interface_id == message.chat.id]
            if room.__len__() == 0:
                return self.bot.send_message(message.chat.id, f'This room is not managed')
            room = room.pop()
            if cmd.__len__() > 1:
                value = cmd[1]
            else:
                value = None
        return self.editroom_recursive(auth, message.chat.id, rooms, room=room,
                                       command=command, value=value, public=self.is_public(message))

    @with_auth
    @with_permission
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
            self.bot.send_message(message.chat.id,
                                  'Send DESTROY to confirm.\n<b>WARNING</b>: This will delete everything.')
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
    """ EVENT SECTION """

    async def local_users(self, command):
        try:
            chat = self.bot.get_chat(command.key.interface_id)
        except telebot.apihelper.ApiTelegramException as e:
            return Response(command_id=command.command_id, error=True, error_message=e.__str__())
        count = self.bot.get_chat_member_count(chat.id)
        localinfo = [user for user in self.activity if self.activity[user]['room'] == command.key]
        resp = Response(command_id=command.command_id, data=localinfo, error=count != localinfo.__len__())
        return resp

    async def kick(self, command):
        try:
            chat = self.bot.get_chat(command.key)
        except telebot.apihelper.ApiTelegramException as e:
            return Response(command_id=command.command_id, error=True, error_message=e.__str__())
        for user in command.value:
            self.bot.kick_chat_member(chat.id, user.interface_id, until_date=time.time()+5)
            self.bot.unban_chat_member(chat.id, user.interface_id)
        return Response(command_id=command.command_id, data=True)

    """ EVENT SECTION END """

    def initialize(self):
        while not self._shutdown:
            self.bot.polling(allowed_updates=telebot.util.update_types)  # fixme configurable delay
            sleep(self.config.polling_delay)

    def local_shutdown(self):
        self.bot.stop_bot()


if __name__ == '__main__':
    from multiprocessing import Queue
    from src.utility.config import Config

    t = Telegram(Queue(), Queue(), Config())
    print([(q.user.id, q.status) for q in t.bot.get_chat_administrators(-1001204546755)])
    # t.run()
