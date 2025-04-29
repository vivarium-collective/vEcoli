"""
============
MIGRATED: Complexation
============

This process encodes molecular simulation of macromolecular complexation,
in which monomers are assembled into complexes. Macromolecular complexation
is done by identifying complexation reactions that are possible (which are
reactions that have sufﬁcient counts of all sub-components), performing one
randomly chosen possible reaction, and re-identifying all possible complexation
reactions. This process assumes that macromolecular complexes form spontaneously,
and that complexation reactions are fast and complete within the time step of the
simulation.
"""

# TODO(wcEcoli):
# - allow for shuffling when appropriate (maybe in another process)
# - handle protein complex dissociation

import numpy as np
from stochastic_arrow import StochasticSystem

from ecoli.library.schema import bulk_name_to_idx, counts
from ecoli.migrated.partition import PartitionedProcess
from ecoli.shared.registry import ecoli_core
from ecoli.shared.utils.schemas import listener_schema, numpy_schema


NAME = "ecoli-complexation"
TOPOLOGY = {"bulk": ("bulk",), "listeners": ("listeners",), "timestep": ("timestep",)}
# ecoli_core.topology.register(NAME, TOPOLOGY)


class Complexation(PartitionedProcess):
    """Complexation PartitionedProcess"""
    name = NAME 
    topology = TOPOLOGY
    defaults = {
        "stoichiometry": np.array([[]]),
        "rates": np.array([]),
        "molecule_names": [],
        "seed": 0,
        "reaction_ids": [],
        "complex_ids": [],
        "time_step": 1,
    }

    def initialize(self, config):
        self.stoichiometry = config["stoichiometry"]
        self.rates = config["rates"]
        self.molecule_names = config["molecule_names"]
        self.reaction_ids = config["reaction_ids"]
        self.complex_ids = config["complex_ids"]

        self.randomState = np.random.RandomState(seed=config["seed"])
        self.seed = self.randomState.randint(2**31)
        self.system = StochasticSystem(self.stoichiometry, random_seed=self.seed)
    
    @property
    def listener_schemas(self) -> dict:
        return {
            "complexation_listener": {
                **listener_schema(
                    {
                        "complexation_events": (
                            [0] * len(self.reaction_ids),
                            self.reaction_ids,
                        )
                    }
                )
            }
        }
    
    def ports_schema(self):
        return {
            "bulk": numpy_schema("bulk"),
            "listeners": self.listener_schemas,
            "timestep": {"_default": self.config["time_step"]},
        }

    def calculate_request(self, state, interval):
        timestep = state["timestep"]
        if self.molecule_idx is None:
            self.molecule_idx = bulk_name_to_idx(
                self.molecule_names, state["bulk"]["id"]
            )

        moleculeCounts = counts(state["bulk"], self.molecule_idx)

        result = self.system.evolve(timestep, moleculeCounts, self.rates)
        updatedMoleculeCounts = result["outcome"]
        requests = {}
        requests["bulk"] = [
            (self.molecule_idx, np.fmax(moleculeCounts - updatedMoleculeCounts, 0))
        ]
        return requests
    
    def evolve_state(self, state, interval):
        timestep = state["timestep"]
        substrate = counts(state["bulk"], self.molecule_idx)

        result = self.system.evolve(timestep, substrate, self.rates)
        complexationEvents = result["occurrences"]
        outcome = result["outcome"] - substrate

        # Write outputs to listeners
        update = {
            "bulk": [(self.molecule_idx, outcome)],
            "listeners": {
                "complexation_listener": {
                    "complexation_events": complexationEvents.astype(int)
                }
            },
        }

        return update
    
    
def test_partition():
    # TODO: here use parameterization from migration.migration_utils in run_partitioned_process
    pass


def test_complexation():
    from ecoli import ecoli_core

    # define config
    test_config = {
        "stoichiometry": [[-1, 1, 0], [0, -1, 1], [1, 0, -1], [-1, 0, 1], [1, -1, 0], [0, 1, -1]],
        "rates": np.random.random((6,)).tolist(),
        "molecule_names": ["A", "B", "C"],
        "seed": 1,
        "reaction_ids": [1, 2, 3, 4, 5, 6],
        "complex_ids": [1, 2, 3, 4, 5, 6],
    }

    complexation = Complexation(config=test_config, core=ecoli_core)

    timestep = 1.0
    state = {
        "bulk": [
            ("A", 10),
            ("B", 20),
            ("C", 30),
        ],
        "timestep": timestep,
    }

    data = complexation.update(state, timestep)
    # complexation_events = data["listeners"]["complexation_listener"][
    #     "complexation_events"
    # ]
    # assert isinstance(complexation_events, list)
    # assert isinstance(complexation_events[1], list), "This is not a list."  # <-- TODO: does this need to be a list with the new structure?
    print(f"Data:\n{data}")

