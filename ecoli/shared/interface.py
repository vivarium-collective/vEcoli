import abc
import copy
from dataclasses import dataclass, field
from functools import wraps
import json

from process_bigraph import Step, Process

from ecoli.shared.data_model import Topology
from ecoli.shared.utils.schemas import get_config_schema, collapse_defaults


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
    _initial_state = {}

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
    
    def get_schema(self):
        schema = copy.deepcopy(self.inputs())
        schema.update(self.outputs())
        return schema


class ProcessBase(EdgeBase, Process):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]
    
    def get_schema(self):
        schema = copy.deepcopy(self.inputs())
        schema.update(self.outputs())
        return schema









