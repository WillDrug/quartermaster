import time
import uuid

from interfaces.interface import Interface
import telebot
from time import sleep
from utility.command import CommandType, Command, Response
from datetime import datetime, timedelta
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
        admins = [q for q in admins if q.status == 'creator']
        if admins.__len__() == 0:
            return None
        member = admins.pop()
    return member


def master_only(func):
    @wraps(func)
    def check_master(self, message):
        # AFTER AUTH (!)
        auth = self.userbase.get(str(message.from_user.id))
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

def get_username(from_user):
    if from_user.username is not None:
        username = from_user.username
    else:
        first = from_user.first_name
        last = from_user.last_name
        if first is None:
            first = ""
        if last is None:
            last = ""
        first = re.sub(r'[^0-9a-zA-Z??-????-??]+', '', first)
        last = re.sub(r'[^0-9a-zA-Z??-????-??]+', '', last)
        username = first + last + from_user.id.__str__()[-5:]
    return username


def with_auth(func):
    @wraps(func)
    def perform_auth(self, message):
        if isinstance(message, telebot.types.CallbackQuery):
            if message.message is not None:
                chat_id = message.message.chat.id
            else:
                chat_id = None
        elif isinstance(message, telebot.types.InlineQuery):
            chat_id = None
        else:
            chat_id = message.chat.id
        if chat_id is not None:
            member = get_member(self.bot, chat_id, message.from_user.id)
            if member is None:
                return
            if member.user != message.from_user:
                message.from_user = member.user
            user_id = member.user.id
            if member.user.is_bot:
                return
        else:
            user_id = message.from_user.id
            if message.from_user.is_bot:
                return
        if user_id not in self.userbase:  # and not member.user.is_bot:
            username = get_username(message.from_user)
            resp = self.auth(message.from_user.id, username)

            if not resp.error:
                self.userbase[message.from_user.id] = resp.data
            else:
                if chat_id is not None:
                    return self.bot.send_message(chat_id, f'There was an error while authenticating you: {resp.error_message}')
                else:
                    print(f'Here a log should be done. Inline query auth failed.')
        if isinstance(self.userbase[user_id], str):  # merging in progress
            return
        return func(self, message)

    return perform_auth


class Telegram(Interface):
    def __init__(self, send_queue, receive_queue, config, *args, **kwargs):
        token = config.telegram_auth
        self.bot = telebot.TeleBot(token, parse_mode='HTML')
        self.handle = ""
        self.prepare_bot()
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

        self.bot.callback_query_handler(lambda call: call.data.startswith('/add_invited'))(self.invite_callback)
        self.bot.callback_query_handler(lambda call: call.data.startswith('/add_roommate'))(self.roommate_callback)

    @with_auth
    def invite_callback(self, call):
        self.bot.edit_message_text(inline_message_id=call.inline_message_id, text=f'Processing your invite')
        return self.inviteroommate_callback(call)

    @with_auth
    def roommate_callback(self, call):
        self.bot.edit_message_text(inline_message_id=call.inline_message_id, text=f'Processing your invite')
        return self.inviteroommate_callback(call, roommate=True)

    def inviteroommate_callback(self, call, roommate=False):
        user = self.users('secret', call.data.split()[1])
        if user.error:
            self.bot.edit_message_text(inline_message_id=call.inline_message_id, text=f'Failed to do the invite:\n')
            return self.bot.answer_callback_query(call.id, f'Failed to find homeowner: {user.error_message}')
        try:
            user = user.data.pop()
        except IndexError:
            self.bot.edit_message_text(inline_message_id=call.inline_message_id,
                                       text=f'Failed to do the invite:\nHomeowner not found')
            return self.bot.answer_callback_query(call.id, f'Failed to find homeowner: no results')
        guest = self.users('secret', call.data.split()[1])
        if guest.error:
            self.bot.edit_message_text(inline_message_id=call.inline_message_id, text=f'Failed to invite: can\'t '
                                                                                      f'find you:\n'
                                                                                      f'{guest.error_message}')
            return self.bot.answer_callback_query(call.id, f'Failed to find you: {guest.error_message}')
        try:
            guest = guest.data.pop()
        except IndexError:
            return self.bot.answer_callback_query(call.id, f'Failed to find you: no results')
        resp = self.add_guest(user, guest, roommate=roommate)
        if resp.error:
            self.bot.edit_message_text(inline_message_id=call.inline_message_id, text=f'Failed to add you:\n'
                                                                                      f'{resp.error_message}')
            return self.bot.answer_callback_query(call.id, f'Failed to add you: {resp.error_message}')
        self.bot.edit_message_text(inline_message_id=call.inline_message_id,
                                   text=f'You have been added as a '
                                        f'{"roommate" if roommate else "guest"}')
        return self.bot.answer_callback_query(call.id, f'You have been added as {"roommate" if roommate else "guest"}')

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
    def show_homes(self, message):  # todo refactor into process_response func.
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
        user_data = []
        for secret in [q.owner for q in homes]:
            appropriate = [q for q in users.data if q.secret == secret and q.interface == self.__class__.__name__] or \
                          [q for q in users.data if q.secret == secret]
            user_data.append(appropriate.pop())
        for user in user_data:
            markup.add(
                telebot.types.InlineKeyboardButton(f'{user.name}\'s Home', callback_data=f'/rooms {user.secret}'))
        return self.inline_menu(chat_id, text='Homes available:', markup=markup, original_message=original_message,
                                callback_id=callback_id, callback_text='Fetched homes', command='/homes')

    @with_auth
    def chat_member_event(self, event: telebot.types.ChatMemberUpdated):
        if event.new_chat_member.status == 'member':
            self._user_joins(event.new_chat_member.user.id, event.new_chat_member.user.username, event.chat.id)
        if event.new_chat_member.status == 'left':
            self._user_leaves(event.new_chat_member.user.id, event.chat.id)
        #self.sync()

    @with_auth
    def save_activity(self, message: telebot.types.Message):  # todo cache managed rooms
        self._save_activity(message.from_user.id, message.from_user.username, message.chat.id)

    @with_auth
    def inline(self, inline_query: telebot.types.InlineQuery):
        res = []
        auth = self.userbase.get(inline_query.from_user.id)
        if inline_query.chat_type == 'private':
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton('Accept Invite', callback_data=f'/add_invited {auth.secret}'))
            res.append(telebot.types.InlineQueryResultArticle(f'{auth.secret}i', 'Invite as a guest',
                                                              telebot.types.InputTextMessageContent(
                                                                  'You have been invited to be a guest.'
                                                              ), reply_markup=markup))
            markup = telebot.types.InlineKeyboardMarkup()
            markup.add(
                telebot.types.InlineKeyboardButton('Accept Invite', callback_data=f'/add_roommate {auth.secret}'))
            res.append(telebot.types.InlineQueryResultArticle(f'{auth.secret}r', 'Invite as a roommate',
                                                              telebot.types.InputTextMessageContent(
                                                                  'You have been invited to be a roommate'
                                                              ), reply_markup=markup))
        rooms = self.get_own_rooms(auth, None)
        if not rooms.error:
            rooms = rooms.data
            for room in rooms:
                markup = telebot.types.InlineKeyboardMarkup(
                    keyboard=[[telebot.types.InlineKeyboardButton(room.name, url=room.address)]]
                )
                res.append(telebot.types.InlineQueryResultArticle(room.name, f'Give {room.name} address',
                                                                  telebot.types.InputTextMessageContent(
                                                                      f'You can join "{room.name}" room via '
                                                                      f'{room.address}'
                                                                  ), reply_markup=markup))
            markup = telebot.types.InlineKeyboardMarkup()
            for room in rooms:
                markup.add(telebot.types.InlineKeyboardButton(room.name, url=room.address))
            txt = 'Here are the rooms of my home'
            res.append(telebot.types.InlineQueryResultArticle('all', 'Send all rooms',
                                                              telebot.types.InputTextMessageContent(txt),
                                                              reply_markup=markup))

        self.bot.answer_inline_query(inline_query.id, res, cache_time=5)

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
        user_id = message.from_user.id
        callback = lambda txt: self.bot.send_message(message.chat.id, txt)
        return self._secret_command(user_id, secret, callback)

    @with_auth
    @with_permission
    def manage_command(self, message):
        if not self.bot.get_chat_member(message.chat.id, self.bot.get_me().id).status in ['administrator', 'creator']:
            return self.bot.reply_to(message, 'I don\'t have enough permissions to manage this group')
        # todo: may be disallow re-creation via /manage and force /delete or /destroy first
        invite = self.bot.get_chat(message.chat.id).invite_link or ''
        return self.bot.send_message(
            message.chat.id,
            self._manage_command(
                self.userbase.get(message.from_user.id),
                re.sub(r'[^0-9a-zA-Z]+', '', message.chat.title),
                invite,
                message.chat.id
            )
        )


    @with_auth
    @with_permission
    def edithome_command(self, message: Union[telebot.types.CallbackQuery, telebot.types.Message]):
        auth = self.userbase.get(message.from_user.id)
        if isinstance(message, telebot.types.Message):
            text = message.text
            chat_id = message.chat.id
        else:
            text = message.data
            chat_id = message.message.chat.id
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
        return self._edithome_recursive(auth, message, chat_id if self.is_public(message) else None, home, command=command, value=value)

    @with_auth
    @with_permission
    def editroom_command(self, message: Union[telebot.types.CallbackQuery, telebot.types.Message]):
        auth = self.userbase.get(message.from_user.id)
        if isinstance(message, telebot.types.Message):
            text = message.text
            chat_id = message.chat.id
        else:
            text = message.data
            chat_id = message.message.chat.id
        room = None
        command = None
        value = None
        cmd = text.split()

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
        if isinstance(room, str):
            room = [q for q in rooms if q.name == room]
            if room.__len__() == 0:
                return self.bot.send_message(chat_id, 'What the...')
            room = room.pop()
        return self._editroom_recursive(auth, message, rooms, not private, room=room, command=command, value=value)

    def process_response(self, target, text=None, reply_text=None, markup=None):
        if markup is not None:
            new_markup = telebot.types.InlineKeyboardMarkup()
            for label, callback in markup:
                new_markup.add(telebot.types.InlineKeyboardButton(text=label, callback_data=callback))
            markup = new_markup
        if isinstance(target, telebot.types.Message):
            self.bot.send_message(target.chat.id, text or reply_text or 'Ok', reply_markup=markup)
        if isinstance(target, telebot.types.CallbackQuery):
            self.bot.answer_callback_query(target.id, reply_text or 'Ok')
            if text is not None:
                self.bot.edit_message_text(text or target.message.text, target.message.chat.id, target.message.id, reply_markup=markup)

    def is_callback(self, target):
        return isinstance(target, telebot.types.CallbackQuery)

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

    def _get_name(self, target: Union[telebot.types.Message, telebot.types.CallbackQuery]):
        if not isinstance(target, telebot.types.Message) and not isinstance(target, telebot.types.CallbackQuery):
            return '*'
        msg = target if isinstance(target, telebot.types.Message) else target.message
        title = msg.chat.title
        return re.sub(r'[^0-9a-zA-Z\*]+', '', title)

    def _get_address(self, target: Union[telebot.types.Message, telebot.types.CallbackQuery]):
        if not isinstance(target, telebot.types.Message) and not isinstance(target, telebot.types.CallbackQuery):
            return '*'
        msg = target if isinstance(target, telebot.types.Message) else target.message
        return msg.chat.invite_link

    def _get_deep_link(self, extra):
        return f'https://t.me/{self.handle}?start={extra}'

    def _add_prompt(self, help_text, target, func, args, kwargs, field):
        if isinstance(target, telebot.types.Message):
            chat_id = target.chat.id
            iid = get_member(self.bot, target.chat.id, target.from_user.id).user.id
        else:
            chat_id = target.message.chat.id
            iid = get_member(self.bot, target.message.chat.id, target.from_user.id).user.id
        self.bot.send_message(chat_id, f'Send me {help_text}')
        self.state[(str(chat_id), str(iid))] = (func, args, kwargs, field)
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
        return self._edithome_recursive(auth, message, message.chat.id if self.is_public(message) else None, home, command=cmd, value=value)

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
        return self._editroom_recursive(auth, message, rooms, self.is_public(message), room=room,
                                        command=command, value=value)

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
        localinfo = [user.interface_id for user in self.activity if self.activity[user]['room'] == command.key]
        resp = Response(command_id=command.command_id, data=localinfo, error=count != localinfo.__len__())
        return resp

    async def kick(self, command):
        try:
            chat = self.bot.get_chat(command.key)
        except telebot.apihelper.ApiTelegramException as e:
            return Response(command_id=command.command_id, error=True, error_message=e.__str__())
        for user in command.value:
            if chat.type in ['supergroup', 'channel']:
                self.bot.unban_chat_member(chat.id, user.interface_id)
            else:
                self.bot.ban_chat_member(chat.id, user.interface_id, until_date=datetime.now() + timedelta(seconds=1))

        return Response(command_id=command.command_id, data=True)

    """ EVENT SECTION END """

    def initialize(self):
        while not self._shutdown:
            self.bot.polling(allowed_updates=telebot.util.update_types)  # fixme configurable delay
            sleep(self.config.polling_delay)
        print(f'Telegram stopped polling')

    def local_shutdown(self):
        self.bot.stop_bot()


if __name__ == '__main__':
    from multiprocessing import Queue
    from src.utility.config import Config

    t = Telegram(Queue(), Queue(), Config())
    print([(q.user.id, q.status) for q in t.bot.get_chat_administrators(-1001204546755)])
    # t.run()
