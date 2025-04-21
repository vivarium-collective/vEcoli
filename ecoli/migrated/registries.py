# TODO: provide clean registration here!

from dataclasses import dataclass
import datetime
from typing import Any

from process_bigraph import ProcessTypes, Process
from vivarium.core.registry import Registry as VivRegistry
from bigraph_schema import Registry as BgsRegistry



class TopologyRegistry(VivRegistry):
    """Maps process names to topology"""
    pass


class ProcessRegistry(BgsRegistry):
    pass


@dataclass
class DataView:
    _processes: ProcessRegistry | BgsRegistry
    _topology: TopologyRegistry | None = None

    @property
    def processes(self):
        return list(self._processes.registry.keys())
    
    @property
    def types(self):
        return(self._processes.registry.keys())
    
    @property
    def topology(self):
        return self._topology.registry if self._topology else {}


class Core(ProcessTypes):
    def __init__(
            self, 
            topology_registry: TopologyRegistry | None = None, 
            *args, 
            **kwargs):
        super().__init__(*args, **kwargs)
        self.last_update: str = self.timestamp()
        self._topology = topology_registry or TopologyRegistry()
        self._view = DataView(self.processes, self.topology)
    
    def timestamp(self):
        return str(datetime.datetime.now())
    
    @property
    def processes(self) -> BgsRegistry:
        return self.process_registry
    
    @property
    def topology(self) -> TopologyRegistry:
        return self._topology
    
    @property
    def view(self) -> DataView:
        """
        Allows for dot notation throughout a get call, ie: Core().view.processes <- gets process names that are registered.
        Possibly view callables are: processes, types, and topology
        """
        return self._view
    
    @view.setter
    def view(self, v):
        raise AssertionError("You cannot set the view.")
    
    
    
    
    
topology_registry = TopologyRegistry()
ecoli_core = Core(topology_registry=topology_registry)

