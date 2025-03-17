from pbg.data_model.base_process import BaseProcess, CORE
from vivarium.core.process import Process


class Add(BaseProcess):
    defaults = {"k": 0.11 / 2.22}
    name = "add"

    def __init__(self, parameters, core=None):
        super().__init__(parameters, core)

    def ports_schema(self):
        return {
            "A": {"x": {"_default": -0.11, "_emit": True}},
            "B": {"y": {"_default": 0.22, "_emit": True}},
        }

    def next_update(self, timestep, states):
        x0 = states["A"]["x"]
        x = x0 * self.parameters["k"] * timestep
        return {"A": {"x": x}, "B": {"y": x0 + x * timestep}}
