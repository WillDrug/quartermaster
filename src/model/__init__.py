import typing

import pydantic
from typing import Union, Optional
from uuid import uuid5, NAMESPACE_OID
import time

class KeyStored:
    def key(self):
        return ''


class DoubleKeyStored(KeyStored):
    dks_field1 = 'interface'
    dks_field2 = 'interface_id'

    @staticmethod
    def make_key(interface, interface_id):
        return str(interface) + str(interface_id)

    def key(self):
        return str(getattr(self, self.dks_field1)) + str(getattr(self, self.dks_field2))

    def __eq__(self, other):
        if isinstance(other, dict):
            oin = other[self.dks_field1]
            oid = other[self.dks_field2]
        elif isinstance(other, User):
            oin = other.interface
            oid = other.interface_id
        else:
            return False
        return getattr(self, self.dks_field1) == oin and getattr(self, self.dks_field2) == oid


class User(pydantic.BaseModel, DoubleKeyStored):
    interface: str
    interface_id: Union[str, int]
    name: str  # for eviction and invite purposes
    secret: pydantic.UUID5

    def __init__(self, **kwargs):
        if 'secret' not in kwargs:
            kwargs['secret'] = uuid5(NAMESPACE_OID, str(kwargs['interface_id']))
        super().__init__(**kwargs)


class Room(pydantic.BaseModel, DoubleKeyStored):
    name: str  # no spaces, daug
    interface: str
    interface_id: Union[int, str]
    owner: pydantic.UUID5
    address: str
    timeout: int = 7200
    invited: list[pydantic.UUID5] = []
    roommates: list[pydantic.UUID5] = []
    locked: bool = False  # requires invite
    closed: bool = False  # fuck off all


class Guest(pydantic.BaseModel, DoubleKeyStored):
    dks_field1: typing.ClassVar[str] = 'user'
    dks_field2: typing.ClassVar[str] = 'room'

    user: pydantic.UUID5  # user secret
    room: str  # room key
    last_sync: int  # time()


class Invite(pydantic.BaseModel, KeyStored):
    creator: pydantic.UUID5
    secret: str
    room: Optional[str]  # room key
    in_chat: Optional[Union[str, int]]  # made for invites from inline to filter from - to only get one.
    roommate: bool = False

    def key(self):
        return self.secret


if __name__ == '__main__':
    u = User(interface='Room', interface_id='test', username='willdrug', secret='b428b5d9-df19-5bb9-a1dc-115e071b836c')
    print(u.secret.__class__('b428b5d9-df19-5bb9-a1dc-115e071b836c') == u.secret)
