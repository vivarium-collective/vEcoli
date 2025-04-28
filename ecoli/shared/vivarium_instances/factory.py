
from dataclasses import dataclass, field
from functools import wraps
import json
import os

from vivarium import Vivarium 

from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.shared.data_model import EmitterConfig
from ecoli.shared.registry import Core, ecoli_core


class VivariumFactory:
    _default_core: Core = ecoli_core

    def __init__(self, default_protocol: str | None = None) -> None:
        self.default_protocol = default_protocol or "community"

    @property
    def default_core(self):
        return self._default_core
    
    def new(
            self, 
            document: dict | None = None, 
            processes: dict | None = None,
            core=None,
            emitter_config: EmitterConfig | None = None
    ) -> Vivarium:
        c = core or self.default_core
        return Vivarium(
            core=c, 
            processes=processes or c.process_registry.registry, 
            types=c.types(), 
            document=document,
            emitter_config=emitter_config.as_dict() if emitter_config else None
        )
    
    def __call__(
            self, 
            document: dict | None = None, 
            processes: dict | None = None, 
            core=None,
            emitter_config: EmitterConfig | None = None
    ) -> Vivarium:
        return self.new(document, processes, core, emitter_config)
    