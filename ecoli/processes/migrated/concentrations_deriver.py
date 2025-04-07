import numpy as np
from scipy.constants import N_A

from process_bigraph import Step
from vivarium.library.units import units, Quantity
from ecoli.library.schema import bulk_name_to_idx, numpy_schema, counts
from ecoli.shared.dtypes import format_bulk_state

AVOGADRO = N_A / units.mol


class ConcentrationsDeriver(Step):
    config_schema = {
        # Bulk molecule names supplied separately so
        # they can be pulled out the Numpy array
        "bulk_variables": "list",
        "variables": "list",
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.bulk_var = self.config["bulk_variables"]
        self.var = self.config["variables"]

        # Helper indices for Numpy indexing
        self.bulk_var_idx = None

    def inputs(self):
        schema = {
            "bulk": "bulk",
            "counts": {
                variable: "integer"  # in counts
                for variable in self.config["variables"]
            },
            "concentrations": {
                variable: "float"
                for variable in self.config["variables"]
            },
            "volume": "float"
        }
        return schema

    def outputs(self):
        schema = {
            "concentrations": {
                variable: "float"
                for variable in self.config["variables"]
            }
        }
        return schema

    def update(self, state):
        bulk_state = format_bulk_state(state)
        if self.bulk_var_idx is None:
            self.bulk_var_idx = bulk_name_to_idx(self.bulk_var, bulk_state["id"])

        volume = Quantity(value=state["volume"], units=units.fL)
        var_concs = {
            var: (count * units.count / AVOGADRO / volume).to(units.millimolar)
            for var, count in state["counts"].items()
        }
        bulk_counts = counts(bulk_state, self.bulk_var_idx)
        bulk_concs = (bulk_counts * units.count / AVOGADRO / volume).to(
            units.millimolar
        )
        new_concs = {**var_concs, **dict(zip(self.bulk_var, bulk_concs))}

        return {
            "concentrations": {
                k: float(v.m)
                for k, v in new_concs.items()
            }
        }


