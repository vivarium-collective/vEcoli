import abc
from dataclasses import dataclass, field
from functools import wraps
import json

from process_bigraph import Step, Process

from ecoli.shared.data_model import Topology
from ecoli.shared.schemas import get_config_schema
from ecoli.shared.vivarium_instances.base import collapse_defaults


class EdgeBase(abc.ABC):
    """We keep the following attrs from the 1.0 implementations:
        - name
        - topology
        - defaults
        - ports_schema
    """
    name = None
    topology = {}  # topology will already be nested
    directed_topology = Topology()
    _captured_state = {}

    def ports_schema(self):
        return {}
    
    def initial_state(self):
        return collapse_defaults(self.ports_schema())

    @classmethod
    def verify_ports(cls, schema: dict) -> bool:
        """
        Checks ports schema (to be used for either inputs or outputs) against the defined topology
        """
        return all([key in list(cls.topology.keys()) for key in schema.keys()])
    

class StepBase(EdgeBase, Step):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]


class ProcessBase(EdgeBase, Process):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]









