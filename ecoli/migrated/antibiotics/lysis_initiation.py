"""
MIGRATED: Lysis Initiation
"""


import numpy as np

from ecoli.library.parameters import param_store
from ecoli.shared.interface import ProcessBase
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import get_defaults_schema


NAME = "ecoli-lysis-initiation"
TOPOLOGY = {
    "cracked": ("wall_state", "cracked"),
    "lysis_trigger": ("lysis_trigger",),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class LysisInitiation(ProcessBase):
    name = NAME

    defaults = {
        "mean_lysis_time": param_store.get(("lysis_initiation", "mean_lysis_time")),
        "seed": 0,
        "time_step": 2,
    }

    def initialize(self, config):
        mean_lysis_time = config["mean_lysis_time"]
        rng = np.random.default_rng(config["seed"])
        self.remaining_time = rng.exponential(mean_lysis_time)

        self.input_ports = {
            "cracked": {"_default": False, "_emit": True},
            "lysis_trigger": {"_default": False, "_emit": True},
        }
        self.output_ports = self.input_ports['lysis_trigger']
    
    def inputs(self):
        return get_defaults_schema(self.input_ports)
    
    def outputs(self):
        return get_defaults_schema(self.output_ports)

    def update(self, interval, state):
        if state["cracked"] and not state["lysis_trigger"]:
            self.remaining_time -= interval
            if self.remaining_time <= 0:
                return {"lysis_trigger": True}

        return {}  # TODO: will this work?
