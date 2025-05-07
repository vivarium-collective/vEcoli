
from abc import abstractmethod
import copy
from vivarium.core.process import Process as VivariumProcess, Step as VivariumStep
from process_bigraph import Process as PbgProcess, Step as PbgStep
from vivarium.core.types import State
from ecoli.shared.utils.schemas import collapse_defaults, get_config_schema, get_defaults_schema


__all__ = [
    'MigrateStep',
    'MigrateProcess',
    'Resolver'
]

class Revert:
    pass 


class Resolver(PbgStep):
    """Takes PartitionedProcess updates and somehow emits 
    a single update that is a resolution of their demands.

    TODO: look at Allocator for Resolver
    """
    pass


class MigrateStep(VivariumStep, PbgStep):
    """This class allows v1 steps to run as v2 steps"""

    config_schema = {} 
    _ports = {
        "inputs": [],
        "outputs": []
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._port_data = self.ports_schema()
        self.input_port_data = self._set_ports("input")
        self.output_port_data = self._set_ports("output")

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
        return ports

    @property 
    def input_ports(self):
        return self._ports["inputs"]
    
    @input_ports.setter
    def input_ports(self, v):
        self._ports["inputs"] = v
    
    @property 
    def output_ports(self):
        return self._ports["outputs"]
    
    @output_ports.setter
    def output_ports(self, v):
        self._ports["outputs"] = v
    
    def inputs(self):
        # extract from ports schema
        return get_defaults_schema(self.input_port_data)

    def outputs(self):
        """Use specific ports if defined, otherwise return bidirectional ports"""
        return get_defaults_schema(self.output_port_data)
    
    def initial_state(self):
        return collapse_defaults(self.input_port_data)
    
    @abstractmethod
    def update(self, state):
        return {}


class MigrateProcess(VivariumProcess, PbgProcess):
    # This class allows v1 processes to run as v2 processes
    config_schema = {} 
    _ports = {
        "inputs": [],
        "outputs": []
    }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._port_data = self.ports_schema()
        self.input_port_data = self._set_ports("input")
        self.output_port_data = self._set_ports("output")

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
        return ports

    @property 
    def input_ports(self):
        return self._ports["inputs"]
    
    @input_ports.setter
    def input_ports(self, v):
        self._ports["inputs"] = v
    
    @property 
    def output_ports(self):
        return self._ports["outputs"]
    
    @output_ports.setter
    def output_ports(self, v):
        self._ports["outputs"] = v
    
    def inputs(self):
        # extract from ports schema
        return get_defaults_schema(self.input_port_data)

    def outputs(self):
        """Use specific ports if defined, otherwise return bidirectional ports"""
        return get_defaults_schema(self.output_port_data)
    
    def initial_state(self):
        return collapse_defaults(self.input_port_data)
    
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

test_migrate_process()


