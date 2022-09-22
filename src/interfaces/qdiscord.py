import math
import time
import typing
import re
from interfaces.interface import Interface
import discord
import asyncio
from functools import wraps
from typing import Union


class Prompt(discord.ui.Modal, title='Enter text'):
    value = discord.ui.TextInput(label='Name')

    def __init__(self, field, callback, *args, **kwargs):
        self.callback = callback
        super().__init__(*args, **kwargs)
        self.value.label = field
        self.title = f'Type in {field}'

    async def on_submit(self, interaction: discord.Interaction):
        #await interaction.response.defer()
        return self.callback(self.value.value, interaction)

    async def on_error(self, interaction, error):
        print(f'Le poo poo {error}')
        return


def with_auth(func):
    @wraps(func)
    def perform_auth(self, message: Union[discord.Message, discord.Member, discord.Interaction]):
        if isinstance(message, discord.Member):
            user = message
        elif isinstance(message, discord.Interaction):
            user = message.user
        else:
            user = message.author
        if user.bot:
            return
        if user.id not in self.userbase:
            resp = self.auth(user.id, user.name)
            if not resp.error:
                self.userbase[user.id] = resp.data
            else:
                if isinstance(message, discord.Message):
                    return message.reply(f'Failed to authenticate you: {resp.error_message}')
        if isinstance(self.userbase[user.id], str):  # merging
            return
        return func(self, message)

    return perform_auth

def master_only(func):
    @wraps(func)
    def check_master(self, message: Union[discord.Message, discord.Member, discord.Interaction]):
        if isinstance(message, discord.Member):
            user = message
        elif isinstance(message, discord.Interaction):
            user = message.user
        else:
            user = message.author
        auth = self.userbase.get(user.id)
        if auth.key() not in self.config.master_ids:
            return
        return func(self, message)

class QuartermasterDiscordClient(discord.Client):

    class Handler:
        def __init__(self, func, commands=(), chat_type=0, use_master_only=False,
                     with_permission=False):
            self.func = func.__func__
            self.instance = func.__self__
            self.func = with_auth(self.func)
            self.commands = commands
            self.chat_type = chat_type
            self.master_only = use_master_only
            self.with_permission = with_permission

        async def __call__(self, message: Union[discord.Message, discord.Interaction]):
            if isinstance(message, discord.Interaction):
                user = message.user
                text = message.data['custom_id']
            else:
                user = message.author
                text = message.content
            if self.with_permission:
                if message.guild is not None and not user.guild_permissions.administrator:
                    return
            if self.master_only:
                self.func = master_only(self.func)
            if self.chat_type > 0 and message.guild is None:
                return
            if self.chat_type < 0 and message.guild is not None:
                return  # todo make command prefix changable
            if self.commands.__len__() > 0 and \
                    not any(text.startswith(f'/{command}') for command in self.commands):
                return
            return await self.func(self.instance, message)

    handlers = []

    def register_handler(self, func, commands=(), only_private=False, only_public=False,
                         use_master_only=False, with_permission=False):
        self.handlers.append(self.Handler(func, commands, only_public - only_private, use_master_only=use_master_only,
                                          with_permission=with_permission))

    async def on_ready(self):
        for guild in self.guilds:
            if not guild.me.guild_permissions.administrator:
                await guild.leave()

    async def on_message(self, message: Union[discord.Message, discord.Interaction]):
        for handler in self.handlers:
            await handler(message)

    @with_auth
    async def on_member_join(self, member):
        pass  # todo implement

    async def on_raw_member_remove(self, member):
        pass  # todo implement


class Discord(Interface):
    def __init__(self, send_queue, receive_queue, config, *args, **kwargs):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        self.bot = QuartermasterDiscordClient(intents=intents)
        self.prepare_bot()
        self.userbase = {}
        self.invites = {}
        super().__init__(send_queue, receive_queue, config, *args, **kwargs)

    def prepare_bot(self):
        self.bot.register_handler(self.secret_command, ('secret',), only_private=True)
        self.bot.register_handler(self.manage_command, ('manage',), only_public=True, with_permission=True)
        self.bot.register_handler(self.edithome_command, ('edithome',), with_permission=True)
        self.bot.register_handler(self.editroom_command, ('editroom',), with_permission=True)

    """ COMMANDS SECTION """

    async def secret_command(self, message: discord.Message):
        cmd = message.content.split()
        secret = None if cmd.__len__() == 1 else cmd[1]
        user_id = message.author.id
        callback = lambda txt: asyncio.create_task(
            message.channel.send(txt.replace('<pre>', '`').replace('</pre>', '`')))

        return self._secret_command(user_id, secret, callback)

    async def manage_command(self, message: discord.Message):
        if not message.guild.me.guild_permissions.kick_members or not message.guild.me.guild_permissions.send_messages or not message.guild.me.guild_permissions.create_instant_invite:
            return asyncio.create_task(message.reply('I don\'t have enough permissions to manage this group'))

        invites = await message.guild.invites()

        if message.guild.vanity_url is not None:
            invite = message.guild.vanity_url
        elif invites.__len__() > 0:
            invite = sorted(invites, key=lambda inv: inv.expires_at or math.inf)[-1].url
        else:
            invite = ''  # todo generate invite
        user = self.userbase.get(message.author.id)
        name = re.sub(r'[^0-9a-zA-Z]+', '', message.guild.name)
        chat_id = message.guild.id
        result = self._manage_command(user, name, invite, chat_id)
        await message.channel.send(result)

    async def edithome_command(self, message: Union[discord.Message, discord.Interaction]):

        target = message
        if isinstance(message, discord.Message):
            user_id = message.author.id
            text = message.content
        else:
            user_id = message.user.id
            text = message.data['custom_id']
        auth = self.userbase.get(user_id)
        cmd = text.split()
        command = None
        value = None
        if cmd.__len__() > 1:
            command = cmd[1]
        if cmd.__len__() > 2:
            value = cmd[2]
        home = self.get_own_home(auth)
        if home.error:
            return self.process_response(target, reply_text=f'Failed to get your home: {home.error_message}')
        home = home.data.pop()

        return self._edithome_recursive(auth, target, message.guild.id, home, command=command, value=value)

    async def editroom_command(self, message: Union[discord.Message, discord.Interaction]):
        if isinstance(message, discord.Message):
            text = message.content
            user_id = message.author.id
        else:
            text = message.data['custom_id']
            user_id = message.user.id
        auth = self.userbase.get(user_id)
        room = None
        command = None
        value = None
        cmd = text.split()

        if message.guild.id not in self.invites or time.time()-self.invites.get(message.guild.id, {}).get('synced', 0) > 180:
            invites = await message.guild.invites()

            if message.guild.vanity_url is not None:
                invite = message.guild.vanity_url
            elif invites.__len__() > 0:
                invite = sorted(invites, key=lambda inv: inv.expires_at or math.inf)[-1].url
            else:
                invite = ''  # todo generate invite
            self.invites[message.guild.id] = {'synced': 0, 'link': invite}


        private = message.guild is None
        rooms = self.get_own_rooms(self.userbase.get(user_id), None)  # fixme cache
        if rooms.error:
            return await self.bot.loop.run_in_executor(None, self.process_response, message, None, 'Failed to get rooms: ' + rooms.error_message)
        rooms = rooms.data
        if not private:
            room = [q for q in rooms if q.interface_id == message.guild.id]
            if room.__len__() == 0:
                return asyncio.create_task(message.channel.send('This room is not managed'))
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

        return self._editroom_recursive(auth, message, rooms, not private, room=room, command=command, value=value)

    def _get_deep_link(self, extra):
        return f'Hey! Send the bot `/start {extra}` to be invited :)'

    def _get_name(self, target: Union[discord.Message, discord.Interaction]):
        if not isinstance(target, discord.Message) and not isinstance(target, discord.Interaction):
            return '*'
        return re.sub(r'[^0-9a-zA-Z]+', '', target.guild.name)

    def _get_address(self, target: Union[discord.Message, discord.Interaction]):
        return self.invites.get(target.guild.id, {}).get('link', '')

    def process_response(self, target: Union[discord.Message, discord.Interaction],
                         text=None, reply_text=None, markup=None):
        view = None
        if markup is not None:
            view = discord.ui.View(timeout=180)

            for label, callback in markup:
                b = discord.ui.Button(label=label, custom_id=callback)
                b.callback = self.bot.on_message
                view.add_item(b)
        if isinstance(target, discord.Message):
            self.bot.loop.create_task(target.channel.send(text or reply_text or 'Ok', view=view))
        if isinstance(target, discord.Interaction):
            if text is not None and reply_text is not None:
                self.bot.loop.create_task(target.message.edit(content=text, view=view))
            elif text is not None:
                self.bot.loop.create_task(target.response.edit_message(content=text, view=view))
            if reply_text is not None:
                self.bot.loop.create_task(target.response.send_message(content=reply_text, ephemeral=True))



    def is_callback(self, target):
        return isinstance(target, discord.Interaction)

    def _add_prompt(self, help_text, target, func, args, kwargs, field_name):
        def callback(value, interaction):
            kwargs[field_name] = value
            nargs = list(args)
            for i in range(len(nargs)):
                if isinstance(nargs[i], discord.Interaction):
                    nargs[i] = interaction
            return func(*nargs, **kwargs)
        modal = Prompt(help_text, callback)
        if isinstance(target, discord.Interaction):
            self.bot.loop.create_task(target.response.send_modal(modal))
        else:
            self.bot.loop.create_task(target.channel.send(f'Specify a value right away or use a menu'))

    """ COMMANDS SECTION END """

    def initialize(self):
        self.bot.run('NzEyNjAzNjgwNzM3ODUzNTIx.GQuvjp.6CBkXZBviefi9Qm0lfW1YFYLNBUfBPa53X3W0Y')

    def local_shutdown(self):
        # fixme: this is not very elegant
        self.bot.loop.create_task(self.bot.close())

    async def kick(self, command):
        print('no kick')

    async def local_users(self, command):
        print('locality!!!')
