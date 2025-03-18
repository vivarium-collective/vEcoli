from pbg.data_model.base_process import BaseProcess
from vivarium.core.process import Process
from vivarium.library.units import units


class Tx(BaseProcess):
    defaults = {"ktsc": 0.01, "kdeg": 0.001}

    def __init__(self, parameters, core=None):
        super().__init__(parameters, core)

    def ports_schema(self):
        return {
            "DNA": {
                "G": {
                    "_default": 10 * units.mg / units.mL,
                    "_updater": "accumulate",
                    "_emit": True,
                }
            },
            "mRNA": {
                "C": {
                    "_default": 100 * units.mg / units.mL,
                    "_updater": "accumulate",
                    "_emit": True,
                }
            },
        }

    def next_update(self, timestep, states):
        G = states["DNA"]["G"]
        C = states["mRNA"]["C"]
        dC = (self.parameters["ktsc"] * G - self.parameters["kdeg"] * C) * timestep
        return {"mRNA": {"C": dC}}
