from enum import Enum, IntEnum
import pydantic
from uuid import uuid4
from typing import Union, Optional
from src.model import User


class CommandType(Enum):
    shutdown = 'shutdown'
    save = 'save'
    auth = 'auth'    # auth = None
    rooms = 'rooms'  # auth: owner\invited\roommate, key: attribute, value: search term
    users = 'users'  # same shit   # interface-bound: key room id, value expected count. response list of users in room
    merge = 'merge'  # auth: requesting user, value: secret; follows the same rules everywhere.
    destroy = 'destroy'  # auth: requesting, key: room or None
    create = 'create'  # auth: owner, key: class, value: dict for class creation
    edit = 'edit'  # auth: owner, key: class, value: duct of changes (must include "key").
    invite = 'invite'  # auth: creator\invitee, key: room or None, value: secret code or None
    invite_clear = 'invite_clear'  # fixme this is horrific
    roommate = 'roommate'  # same shit
    evict = 'evict'  # auth, key: room or None, value: username   interface bound key room value list of kicks
    sync = 'sync'  # no auth, value: user_ids: time
    join = 'join'
    leave = 'leave'
    home = 'home'


auth_required = ['home', 'rooms', 'merge', 'destroy', 'create', 'edit', 'invite', 'roommate', 'evict',
                 'invite', 'invite_clear']  # shutdown?


class Command(pydantic.BaseModel):
    command_id: str
    command_type: CommandType
    auth: Optional[User]
    key: Optional[Union[int, str]]
    value: Optional[Union[pydantic.UUID5, int, str, list, dict]]  # is this an OBJECT at this point?

    @pydantic.validator('auth')
    def auth_required(cls, value, values):
        chk = values.get('command_type')
        assert chk is not None, 'command_type mandatory for Command'
        if chk in auth_required:
            assert value is not None, chk.value+' command requires auth!'
        return value

    def __init__(self, **kwargs):
        if 'command_id' not in kwargs:
            kwargs['command_id'] = uuid4().__str__()
        super().__init__(**kwargs)


class Response(pydantic.BaseModel):
    command_id: str
    data: Union[list, object] = None
    error: bool = False
    error_message: str = ""

if __name__ == '__main__':
    for k in CommandType:
        print(k.value)
    cmd = Command(interface='test', command_type='shutdown', auth=None, key=2)
    resp = Response(command_id='test', data=cmd)
    print(resp)