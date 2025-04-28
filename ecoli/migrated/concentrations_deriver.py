from scipy.constants import N_A

from ecoli.shared.interface import StepBase
from vivarium.library.units import units, Quantity
from ecoli.library.schema import bulk_name_to_idx, counts
from ecoli.shared.schemas import numpy_schema

AVOGADRO = N_A / units.mol


class ConcentrationsDeriver(StepBase):
    defaults: dict[str, list[str]] = {
        # Bulk molecule names supplied separately so
        # they can be pulled out the Numpy array
        "bulk_variables": [],
        "variables": [],
    }
    name = "concentrations_deriver"

    def __init__(self, config=None, core=None):
        super().__init__(config)
        self.bulk_var = self.config["bulk_variables"]
        self.var = self.config["variables"]
        # Helper indices for Numpy indexing
        self.bulk_var_idx = None

    def inputs(self):
        schema = {
            "bulk": numpy_schema("bulk"),
            "counts": {
                variable: "integer"
                for variable in self.config["variables"]
            },
            "concentrations": {
                variable: {
                    "_default": 0 * units.mM,
                    "_type": "unum"
                }
                for variable in self.config["variables"]
            },
            "volume": {
                "_default": 0 * units.fL,
                "_type": "unum"
            },
        }
        return schema
    
    def outputs(self):
        schema = {
            "concentrations": {
                variable: {
                    "_default": 0 * units.mM,
                    "_type": "unum"
                }
                for variable in self.config["variables"]
            }
        }
        return schema

    def update(self, state):
        if self.bulk_var_idx is None:
            self.bulk_var_idx = bulk_name_to_idx(self.bulk_var, state["bulk"]["id"])
        volume = state["volume"]
        assert isinstance(volume, Quantity)
        var_concs = {
            var: (count * units.count / AVOGADRO / volume).to(units.millimolar)
            for var, count in state["counts"].items()
        }
        bulk_counts = counts(state["bulk"], self.bulk_var_idx)
        bulk_concs = (bulk_counts * units.count / AVOGADRO / volume).to(
            units.millimolar
        )
        new_concs = {**var_concs, **dict(zip(self.bulk_var, bulk_concs))}
        update = {"concentrations": new_concs}
        return update
