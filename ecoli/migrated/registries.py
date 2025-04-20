# TODO: provide clean registration here!

from dataclasses import dataclass
from typing import Any

from process_bigraph import ProcessTypes


#: Maps process names to topology
# topology_registry = Registry()


@dataclass
class Get:
    core: ProcessTypes | Any

    @property
    def processes(self):
        return list(self.core.process_registry.registry.keys())
    
    @property
    def types(self):
        return(self.core.process_registry.registry.keys())
    

class Core(ProcessTypes):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    
    @property
    def get(self):
        return Get(core=self)
    
    @property
    def processes(self):
        return self.process_registry
    
    
ecoli_core = Core()