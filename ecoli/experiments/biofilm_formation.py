# This script was an initial way on how to create biofilm formation prior to switching to fully exploring multibody physics and pymunk
# Has not been updated since 09/24/25 --> since then switched to cloning the multibody repository and testing that for biofilm formation, will come back to this to execute biofilm formation when wiring is ready
# This was also a good demo on how to write a Vivarium Process -- my first one


import copy

from vivarium.core.process import Process

# composites - python classes


# plotting
# from vivarium_multibody.processes import *


# Write a class for biofilm formation here


# what defaults do we need here?
# start simple -- cells on a surface that uptake glucose
# before the composer is made we need to define and make the processes we want


class GrowthUptake(Process):
    defaults = {
        "k_growth": 1e-5,
        "k_glc_uptake": 2.7e-3,
    }

    def __init__(self, config=None):
        if config is None:
            config = {}
        parameters = copy.deepcopy(GrowthUptake.defaults)
        parameters.update(config)
        super().__init__(parameters)

    def ports_schema(self):
        return {
            "agents": {
                "*": {
                    "location": {"_default": [0.5, 0.5]},
                    "biomass": {"_default": 2.0},
                }
            },
            "surface": {
                "y_wall": {"_default": 0.0},
                "x_wall": {"_default": 10.0},  # surface glucose
                "decay_y": {"_default": 2.0},  # decay length
            },
        }

    def next_update(self, timestep, states):
        agents = states["agents"]

        updates = {"agents": {}}

        # TODO: implement glucose gradient-driven growth rate
        # y_wall = states["y_wall"]["surface"]
        # surface_glucose = states["surface"]["x_wall"]
        # k_growth = self.parameters["k_growth"]
        # for each agent compute: y = max(0.0, a["location"][1] - y_wall)
        # S = surface_glucose * math.exp(-y / max(lam, 1e-12))
        # dx = k_growth * S * timestep

        for _aid, _a in agents.items():
            pass  # TODO: apply glucose gradient growth rate update

        return updates


# Started to make a film composer --> too ambitious right now, so commented this out
# class Biofilm(Composer):
#  defaults = {

# }

# def __init__(self, config):
#    super().__init__(config)


# def generate_processes(self,config):
#   return {
#     ''
# }

# def generate_topology(self, config: Optional[dict]) -> Topology:
#  return Topology()
