"""
MockSecretion — a self-contained demo process added via a vEcoli fork to
exercise the config-driven vEcoli<->v2ecoli comparison harness.

It accumulates elapsed simulation time into a dedicated listener store at a
configurable rate. It reads only ``global_time`` and writes only its own
listener path, so it is scientifically INERT: it does not perturb the
whole-cell model's dynamics. Its purpose is to validate that a new Vivarium-1.0
process can be added to a vEcoli fork via config and automatically translated +
injected into the v2ecoli composite by the comparison harness.
"""

from vivarium.core.process import Process

from ecoli.processes.registries import topology_registry

NAME = "ecoli-mock-secretion"

# Topology uses store paths that exist in BOTH the vEcoli and the v2ecoli
# composites (shared store layout): the root ``global_time`` store and a fresh
# leaf under ``listeners``.
TOPOLOGY = {
    "global_time": ("global_time",),
    "secreted": ("listeners", "mock_secretion", "secreted"),
}
topology_registry.register(NAME, TOPOLOGY)


class MockSecretion(Process):
    """Accumulates ``rate * timestep`` into a listener every step."""

    name = NAME
    topology = TOPOLOGY

    defaults = {"rate": 1.0, "time_step": 1.0}

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.rate = self.parameters["rate"]

    def ports_schema(self):
        return {
            "global_time": {"_default": 0.0},
            "secreted": {
                "_default": 0.0,
                "_updater": "accumulate",
                "_emit": True,
            },
        }

    def next_update(self, timestep, states):
        return {"secreted": self.rate * timestep}
