import abc
import copy
from dataclasses import dataclass, field
from functools import wraps
import json

from process_bigraph import Step, Process

from ecoli.shared.data_model import Topology
from ecoli.shared.utils.schemas import get_config_schema, collapse_defaults, get_defaults_schema


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


class ListenerBase(StepBase, abc.ABC):
    defaults = {
        "time_step": 1,
        "emit_unique": False,
    }
    _input_ports = {}
    _output_ports = {}

    @property
    @abc.abstractmethod
    def input_ports(self):
        return self._input_ports
    
    @input_ports.setter
    def input_ports(self, ports):
        self._input_ports = ports
    
    @property
    @abc.abstractmethod
    def output_ports(self):
        return self._output_ports
    
    @output_ports.setter
    def output_ports(self, ports):
        self._output_ports = ports
    
    def inputs(self):
        return get_defaults_schema(self.input_ports)
    
    def outputs(self):
        return get_defaults_schema(self.output_ports)
    
    def initial_state(self):
        return collapse_defaults(self.output_ports)
    

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









