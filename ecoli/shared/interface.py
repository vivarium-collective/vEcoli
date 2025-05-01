"""
NOTE: The following subclasses should only be used if needed. Regular process bigraph Processes and Steps will work fine if the implementation is simple enough.
"""


import abc
import copy
from dataclasses import dataclass, field
from functools import wraps
import json
from warnings import warn

from process_bigraph import Step, Process

from ecoli.shared.data_model import Topology
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import get_config_schema, collapse_defaults, get_defaults_schema



from abc import abstractmethod
import copy
from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep
from process_bigraph import Process as PbgProcess, Step as PbgStep
from vivarium.core.types import State
from ecoli.shared.utils.schemas import collapse_defaults, get_config_schema, get_defaults_schema


class MetaABCAndType(abc.ABCMeta, type):
    pass


class Revert:
    pass 


class Resolver(PbgStep):
    """Takes PartitionedProcess updates and somehow emits 
    a single update that is a resolution of their demands.

    TODO: look at Allocator for Resolver
    """
    pass


class MigrateStep(PbgStep, VivariumStep, metaclass=MetaABCAndType):
    """This class allows v1 steps to run as v2 steps.
    NOTE: Users that create new Steps should inherit this class and then specify _output keys in the
    v1 process implementation's ports_schema() method.
    """
    topology = {}
    _ports = {
        "inputs": [],
        "outputs": []
    }
        

    def __init__(self, parameters=None, core=None) -> None:
        VivariumProcess.__init__(self, parameters=parameters)
        super().__init__(config=parameters, core=core or ecoli_core)
        self._port_data = self.ports_schema()
        self._input_port_data = self._set_ports("inputs")
        self._output_port_data = self._set_ports("outputs")

    def __init_subclass__(cls, **kwargs): 
        cls.config_schema = {
            **get_config_schema(cls.defaults),
            "time_step": {"_default": 1.0, "_type": "float"}
        }
    
    def _set_ports(self, port_type: str):
        """Separates inputs from outputs and defines defaults"""
        port_names = getattr(self, f"{port_type}_ports")
        ports = copy.deepcopy(self._port_data)
        if len(port_names):
            ports = {
                port: self._port_data[port]
                for port in port_names
            }
        else:
            warn(f"You have not defined any explicit {port_type}")
        return ports

    @property 
    def input_ports(self) -> list[str]:
        return self._ports["inputs"]
    
    @input_ports.setter
    def input_ports(self, v):
        self._ports["inputs"] = v
    
    @property 
    def output_ports(self) -> list[str]:
        return self._ports["outputs"]
    
    @output_ports.setter
    def output_ports(self, v):
        self._ports["outputs"] = v
    
    def inputs(self):
        # extract from ports schema
        return get_defaults_schema(self._input_port_data)

    def outputs(self):
        """Use specific ports if defined, otherwise return bidirectional ports"""
        return get_defaults_schema(self._output_port_data)
    
    def initial_state(self):
        return collapse_defaults(self._input_port_data)
    
    def update(self, state):
        return self.next_update(states=state, timestep=0)


class MigrateProcess(PbgProcess, VivariumProcess, metaclass=MetaABCAndType):
    # This class allows v1 processes to run as v2 processes
    topology = {}
    _ports = {
        "inputs": [],
        "outputs": []
    }

    def __init__(self, parameters=None, core=None) -> None:
        VivariumProcess.__init__(self, parameters=parameters)
        super().__init__(config=parameters, core=core or ecoli_core)
        self._port_data = self.ports_schema()
        self._input_port_data = self._set_ports("inputs")
        self._output_port_data = self._set_ports("outputs")

    def __init_subclass__(cls, **kwargs): 
        cls.config_schema = {
            **get_config_schema(cls.defaults),
            "time_step": {"_default": 1.0, "_type": "float"}
        }
    
    def _set_ports(self, port_type: str):
        """Separates inputs from outputs and defines defaults"""
        port_names = getattr(self, f"{port_type}_ports")
        ports = copy.deepcopy(self._port_data)
        if len(port_names):
            ports = {
                port: self._port_data[port]
                for port in port_names
            }
        else:
            warn(f"You have not defined any explicit {port_type}")
        return ports

    @property 
    def input_ports(self) -> list[str]:
        return self._ports["inputs"]
    
    @input_ports.setter
    def input_ports(self, v):
        self._ports["inputs"] = v
    
    @property 
    def output_ports(self) -> list[str]:
        return self._ports["outputs"]
    
    @output_ports.setter
    def output_ports(self, v):
        self._ports["outputs"] = v
    
    def inputs(self):
        # extract from ports schema
        return get_defaults_schema(self._input_port_data)

    def outputs(self):
        """Use specific ports if defined, otherwise return bidirectional ports"""
        return get_defaults_schema(self._output_port_data)
    
    def initial_state(self):
        return collapse_defaults(self._input_port_data)
    
    def update(self, state, interval):
        return self.next_update(interval, state)
    

def test_migrate_process():
    class Test(MigrateProcess):
        defaults = {'k': 0.11}

        def __init__(self, config, core=None):
            super().__init__(config)
            self.input_ports = ['x', 'y']
            self.output_ports = ['z']
        
        def ports_schema(self):
            return {
                'x': {'_default': 11.11},
                'y': {'_default': 2.22},
                'z': {'_default': 3}
            }

        def next_update(self, timestep, states):
            state = states
            return {
                'z': state['x']**state['y'] / timestep*2
            }
    
    proc = Test({})
    results = proc.update(proc.initial_state(), 11)
    print(results)
    print(proc.inputs())


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
    timestep_schema = {"_default": 1.0, "_type": "float"}

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
    

# --- steps --- # 

class StepBase(EdgeBase, Step):
    defaults = {}
    config_schema = {}
    _input_ports = {}
    _output_ports = {}

    def __init_subclass__(cls, **kwargs): 
        cls.config_schema = {
            **get_config_schema(cls.defaults),
            "time_step": {"_default": 1.0, "_type": "float"}
        }

    def __init__(self, config=None, core=None):
        super().__init__(config, core or ecoli_core)
        self.timestep_schema = self.config_schema["time_step"]
        self.timestep = self.timestep_schema['_default']
    
    def get_schema(self):
        schema = copy.deepcopy(self.inputs())
        schema.update(self.outputs())
        return schema
    
    @property
    def input_ports(self):
        return self._input_ports
    
    @input_ports.setter
    def input_ports(self, ports):
        self._input_ports = ports
    
    @property
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
        return collapse_defaults(self.input_ports)


class ListenerBase(StepBase, abc.ABC):
    defaults = {
        "time_step": 1,
        "emit_unique": False,
    }
    _input_ports = {}
    _output_ports = {}

    @property
    def input_ports(self):
        return self._input_ports
    
    @input_ports.setter
    def input_ports(self, ports):
        self._input_ports = ports
    
    @property
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
        return collapse_defaults(self.input_ports)
    

# --- processes --- # 
# TODO: should we apply the listenerbase interface to 1 level of inheritance prior? 



class ProcessBase(EdgeBase, Process):
    defaults = {}
    _input_ports = {}
    _output_ports = {}

    def __init_subclass__(cls, **kwargs): 
        cls.config_schema = {
            **get_config_schema(cls.defaults),
            "time_step": {"_default": 1.0, "_type": "float"}
        }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.timestep_schema = self.config_schema["time_step"]
        self.timestep = self.timestep_schema['_default']
    
    def get_schema(self):
        schema = copy.deepcopy(self.inputs())
        schema.update(self.outputs())
        return schema
    
    @property
    def input_ports(self):
        return self._input_ports
    
    @input_ports.setter
    def input_ports(self, ports):
        self._input_ports = ports
    
    @property
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
        return collapse_defaults(self.input_ports)


def test_step_base():
    # it passes :)
    class TestStep(StepBase):
        defaults = {"k": 11.11}

        def initialize(self, config):
            self.input_ports = {
                "x": {"_default": 22.2},
                "y": {"_default": 1.1122}
            }

            self.output_ports = {"z": {"_default": 3.0}}
        
        def update(self, state):
            return {
                "z": state['x']**state['y']
            }
        
    step = TestStep({}, core=ecoli_core)
    expected_config = {'k': {'_type': 'float', '_default': 11.11}, 'time_step': {'_default': 1.0, '_type': 'float'}}
    expected_inputs, expected_outputs = {'x': 'float', 'y': 'float'}, {'z': 'float'}
    assert step.config_schema == expected_config and step.inputs() == expected_inputs
    result = step.update(step.initial_state())
    assert result.keys() == expected_outputs.keys()

    







