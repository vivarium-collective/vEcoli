from dataclasses import dataclass, asdict, field, astuple
import json
from typing import Any
import pickle


@dataclass
class BaseClass:
    def serialize(self) -> str:
        d = self.as_dict()
        return json.dumps(d)

    def as_bytes(self):
        return pickle.dumps(self.as_dict())

    def as_dict(self):
        return asdict(self)

    def as_tuple(self):
        return astuple(self)
    
    @classmethod
    def hydrate(cls, p: bytes) -> dict:
        return pickle.loads(p)

    @property
    def _builtins(self):
        return ['hydrate', 'bytes', 'dict', 'tuple', 'get_attrs']

    def get_attrs(self):
        return [attr for attr in dir(self) if not attr in self._builtins and not attr.startswith("_")]


# --- composition --- #

@dataclass
class EmitterConfig(BaseClass):
    address: str 
    config: dict = field(default_factory=dict)  # config schema
    mode: str = "all"
    path: tuple[str] = ("emitter",)


@dataclass 
class Topology(BaseClass):
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)


# --- API Requests/Responses/etc --- # 

@dataclass
class VivariumDocument(BaseClass):
    state: dict[str, Any] = field(default_factory=dict)
    composition: str | None = None


@dataclass
class IntervalResult(BaseClass):
    def __init__(self, **port_data):
        for port_name, port_value in port_data.items():
            setattr(self, port_name, port_value)

    @property
    def _builtins(self):
        return super()._builtins + ["ports"]

    @property
    def ports(self):
        return self.get_attrs()


@dataclass
class SimulationResult(BaseClass):
    simulation_id: str
    timestamp: str
    data: list[IntervalResult]


