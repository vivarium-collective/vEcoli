"""
====================
MIGRATED: Two Component System
====================

This process models the phosphotransfer reactions of signal transduction pathways.

Specifically, phosphate groups are transferred from histidine kinases to response regulators
and back in response to counts of ligand stimulants.
"""

import numpy as np

from ecoli.library.schema import numpy_schema, bulk_name_to_idx, counts

from wholecell.utils import units
from ecoli.processes.registries import topology_registry
from ecoli.processes.migrated.partition import PartitionedProcess


# Register default topology for this process, associating it with process name
# NAME = "ecoli-two-component-system"
# TOPOLOGY = {
#     "listeners": ("listeners",),
#     "bulk": ("bulk",),
#     "timestep": ("timestep",),
# }
# topology_registry.register(NAME, TOPOLOGY)


class TwoComponentSystem(PartitionedProcess):
    """Two Component System PartitionedProcess"""

    config_schema = {
        "jit": "boolean",
        "n_avogadro": "float",
        "cell_density": "float",
        "moleculeNames": "list",
        "seed": "integer",
    }

    # Constructor
    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        # Simulation options
        self.all_molecule_changes = None
        self.molecules_required = None
        self.cellVolume = None
        self.jit = self.config.get("jit", False)

        # Get constants
        self.n_avogadro = self.config["n_avogadro"]
        self.cell_density = self.config["cell_density"]

        # Create method
        self.moleculesToNextTimeStep = (
            lambda counts, volume, avogadro, timestep, random, method, min_step, jit: (
                [],
                [],
            )
        )

        # Build views
        self.moleculeNames = self.config["moleculeNames"]

        self.seed = self.config["seed"]
        self.random_state = np.random.RandomState(seed=self.seed)

        # Helper indices for Numpy indexing
        self.molecule_idx = None

    def inputs(self):
        return {
            "bulk": "bulk",  # numpy_schema("bulk"),
            "listeners": "tree",  # {"mass": {"cell_mass": {"_default": 0}}},
            "timestep": "float",
        }

    def outputs(self):
        return {
            "bulk": "bulk",  # numpy_schema("bulk"),
        }

    def calculate_request(self, state):
        # At t=0, convert all strings to indices
        if self.molecule_idx is None:
            self.molecule_idx = bulk_name_to_idx(
                self.moleculeNames, state["bulk"]["id"]
            )

        # Get molecule counts
        moleculeCounts = counts(state["bulk"], self.molecule_idx)

        # Get cell mass and volume
        cellMass = (state["listeners"]["mass"]["cell_mass"] * units.fg).asNumber(
            units.g
        )
        self.cellVolume = cellMass / self.cell_density

        # Solve ODEs to next time step using the BDF solver through solve_ivp.
        # Note: the BDF solver has been empirically tested to be the fastest
        # solver for this setting among the list of solvers that can be used
        # by the scipy ODE suite.
        self.molecules_required, self.all_molecule_changes = (
            self.moleculesToNextTimeStep(
                moleculeCounts,
                self.cellVolume,
                self.n_avogadro,
                state["timestep"],
                self.random_state,
                method="BDF",
                jit=self.jit,
            )
        )
        requests = {"bulk": [(self.molecule_idx, self.molecules_required.astype(int))]}
        return requests

    def update(self, timestep, states):
        moleculeCounts = counts(states["bulk"], self.molecule_idx)
        # Check if any molecules were allocated fewer counts than requested
        if (self.molecules_required > moleculeCounts).any():
            _, self.all_molecule_changes = self.moleculesToNextTimeStep(
                moleculeCounts,
                self.cellVolume,
                self.n_avogadro,
                10000,
                self.random_state,
                method="BDF",
                # min_time_step=states["timestep"],
                jit=self.jit,
            )
        # Increment changes in molecule counts
        update = {"bulk": [(self.molecule_idx, self.all_molecule_changes.astype(int))]}

        return update
