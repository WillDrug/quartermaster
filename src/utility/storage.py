from abc import ABCMeta, abstractmethod
from typing import Union, Any, Iterable, Callable
from src.model import KeyStored
import shelve

class Storage(metaclass=ABCMeta):
    @classmethod
    def impl_list(cls):
        return {q.__name__: q for q in cls.__subclasses__()}

    def __init__(self, cls_stored: type):
        self.cls_stored = cls_stored

    @abstractmethod
    def get(self, cid: Union[str, int]) -> KeyStored:
        pass

    @abstractmethod
    def upsert(self, new_obj: KeyStored) -> None:  # fixme: this fails on concurrency
        pass

    @abstractmethod
    def search(self, attr_name: str, value: Any) -> list[KeyStored]:
        pass

    @abstractmethod
    def search_func(self, func: Callable) -> list[KeyStored]:
        pass

    @abstractmethod
    def delete(self, key: str):
        pass

    @abstractmethod
    def delete_via_attr(self, key: str, value: Any) -> None:
        pass

    @abstractmethod
    def delete_via_obj(self, objs: Iterable[KeyStored]):
        pass


class InMemory(Storage):

    def __init__(self, cls_stored: type):
        assert hasattr(cls_stored, 'key')
        self.data = {}
        super().__init__(cls_stored)

    def search(self, attr_name: str, value: Any) -> list[KeyStored]:
        ret = []
        for k in self.data:
            try:
                attr = getattr(self.data[k], attr_name)
                if not isinstance(value, attr.__class__):
                    value = attr.__class__(value)
                if attr == value:
                    ret.append(self.data[k])
            except AttributeError as e:  # fixme: logging
                print(e)
            except ValueError as e:
                print(e)
        return ret

    def search_func(self, func: Callable) -> list[KeyStored]:
        ret = []
        for k in self.data:
            if func(self.data[k]):
                ret.append(self.data[k])
        return ret

    def get(self, cid: Union[str, int]) -> KeyStored:
        cid = str(cid)
        return self.data.get(cid)

    def upsert(self, new_obj: KeyStored) -> None:
        assert isinstance(new_obj, KeyStored), f'{new_obj} is not of type {self.cls_stored}'

        self.data[new_obj.key()] = new_obj

    def delete(self, key: str):
        del self.data[key]

    def delete_via_attr(self, key: str, value: Any) -> None:
        dumplist = []
        for o in self.data:
            if hasattr(self.data[o], key):
                if getattr(self.data[o], key) == value:
                    dumplist.append(o)
        for k in dumplist:
            del self.data[k]

    def delete_via_obj(self, objs: Iterable):
        for o in objs:
            del self.data[o.key()]




class ShelveStorage(InMemory):
    def __init__(self, cls_stored):
        super().__init__(cls_stored)
        self.data = shelve.open(f'data/{cls_stored.__name__.lower()}s')

    def __del__(self):
        self.data.close()

if __name__ == '__main__':
    class IntStored(int, KeyStored):
        def key(self):
            return self
    m = InMemory(IntStored)
    m.upsert(IntStored(5))
    print(m.search_func(lambda o: True)[0].key())
