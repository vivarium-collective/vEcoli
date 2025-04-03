from dataclasses import dataclass, asdict, field, astuple
from typing import Any
import pickle


@dataclass
class Base:
    @classmethod
    def hydrate(cls, p: bytes) -> dict:
        return pickle.loads(p)

    def bytes(self):
        return pickle.dumps(self.dict())

    def dict(self):
        return asdict(self)

    def tuple(self):
        return astuple(self)

    @property
    def _builtins(self):
        return ['hydrate', 'bytes', 'dict', 'tuple', 'get_attrs']

    def get_attrs(self):
        return [attr for attr in dir(self) if not attr in self._builtins and not attr.startswith("_")]


@dataclass
class VivariumDocument(Base):
    state: dict[str, Any]
    composition: str


@dataclass
class IntervalResult(Base):
    def __init__(self, **port_data):
        for port_name, port_value in port_data.items():
            setattr(self, port_name, port_value)

    @property
    def _builtins(self):
        return super()._builtins + ["ports"]

    @property
    def ports(self):
        return self.get_attrs()

