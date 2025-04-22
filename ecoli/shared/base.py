import abc
from dataclasses import dataclass

from process_bigraph import Step, Process
from vivarium.vivarium import Vivarium

from ecoli.shared.datamods import BaseClass
from ecoli.shared.schemas import get_config_schema
from ecoli.shared.registration import Core, ecoli_core



@dataclass 
class Topology(BaseClass):
    inputs: dict
    outputs: dict
    

class StepBase(Step):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state):
        pass

    @property
    def topology(self):
        get_topology = lambda port: {k: [k] for k, v in port.items()}
        return Topology(**dict(zip(
            ["inputs", "outputs"],
            list(map(
                get_topology,
                [self.inputs(), self.outputs()]
            ))
        )))


class ProcessBase(Process):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state, interval):
        pass

    @property
    def topology(self):
        get_topology = lambda port: {k: [k] for k, v in port.items()}
        return Topology(**dict(zip(
            ["inputs", "outputs"],
            list(map(
                get_topology,
                [self.inputs(), self.outputs()]
            ))
        )))


class VivariumFactory:
    _default_core: Core = ecoli_core

    def __init__(self, default_protocol: str | None = None) -> None:
        self.default_protocol = default_protocol or "community"

    @property
    def default_core(self):
        return self._default_core
    
    def new(self, document: dict | None = None, core=None) -> Vivarium:
        c = core or self.default_core
        return Vivarium(
            core=c, 
            processes=c.process_registry.registry, 
            types=c.types(), 
            document=document
        )
    
    def __call__(self, document: dict | None = None, core=None) -> Vivarium:
        return self.new(document, core)


vivarium_factory = VivariumFactory()
