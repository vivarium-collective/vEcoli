"""
============
Complexation
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
from process_bigraph import Process, ProcessTypes

from ecoli.library.schema import numpy_schema, bulk_name_to_idx, counts, listener_schema


class Complexation(Process):
    """Complexation Process"""

    config_schema = {
        "rates": "list[float]",
        "stoichiometry": "list",
        "molecule_names": "list",
        "seed": "integer",
        "reaction_ids": "list",
        "complex_ids": "list",
        "time_step": {
            "_type": "float",
            "_default": 1.0,
        }
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        self.stoichiometry = np.array(self.config["stoichiometry"])
        self.rates = np.array(self.config["rates"])
        self.molecule_names = self.config["molecule_names"]
        self.molecule_idx = None
        self.reaction_ids = self.config["reaction_ids"]
        self.complex_ids = self.config["complex_ids"]

        self.randomState = np.random.RandomState(seed=self.config["seed"])
        self.seed = self.randomState.randint(2**31)
        self.system = StochasticSystem(self.stoichiometry, random_seed=self.seed)

        # TODO: create a base class that has this and inherit it for this class
        self.bulk_dtype = np.dtype([
            ("id", "<U100"),
            ("count", "<f8")
        ])

    def initial_state(self):
        return {
            "bulk": [()],
            "timestep": self.config["time_step"],
        }

    def inputs(self):
        """A test for this process defines a mock bulk port as:
        "bulk": np.array(
        #     [
        #         ("A", 10),
        #         ("B", 20),
        #         ("C", 30),
        #     ],
        #     dtype=[("id", "U40"), ("count", int)],

        ...so a list[{id: , count: }]
        """
        return {
            "bulk": "list[bulk_type]",
            "timestep": "float",
        }

    def outputs(self):
        return {
            "bulk": "list[bulk_type]",
            "listeners": "tree[complexation_listener_type]"  # {"complexation_listener": "complexation_listener_type",}
        }

    def requester_inputs(self):
        return self.inputs()

    def requester_outputs(self):
        # TODO: these need to match that returned by calculate_request, right?
        return {
            "bulk": "list[bulk_type]"
        }

    def calculate_request(self, state):
        timestep = state["timestep"]
        bulk_state: np.ndarray[tuple] = np.array(state["bulk"], dtype=self.bulk_dtype)

        if self.molecule_idx is None:
            self.molecule_idx = bulk_name_to_idx(  # TODO: can we make this non-brittle?
                self.molecule_names, bulk_state["id"]  # NOTE: keep in mind that here ["id"] is an index NOT dict key
            )

        moleculeCounts = counts(bulk_state, self.molecule_idx)

        result = self.system.evolve(timestep, moleculeCounts, self.rates)
        updatedMoleculeCounts = result["outcome"]
        molecule_idx = self.molecule_idx.tolist() if isinstance(self.molecule_idx, np.ndarray) else self.molecule_idx
        requests = {
            "bulk": [
                (molecule_idx, np.fmax(moleculeCounts - updatedMoleculeCounts, 0))
            ]
        }
        return requests

    def update(self, state, interval):
        timestep = state["timestep"]
        bulk_state = np.array(state["bulk"], dtype=self.bulk_dtype)
        substrate = counts(bulk_state, self.molecule_idx).flatten().astype(np.int64)

        result = self.system.evolve(timestep, substrate, self.rates)
        complexationEvents = result["occurrences"]
        outcome = result["outcome"] - substrate

        # Write outputs to listeners
        update = {
            "bulk": [(self.molecule_idx, outcome)],
            "listeners": {
                "complexation_listener": {
                    "complexation_events": complexationEvents.astype(int).tolist()
                }
            },
        }

        return update


def test_complexation():
    from ecoli import ecoli_core

    # define config
    test_config = {
        "stoichiometry": [[-1, 1, 0], [0, -1, 1], [1, 0, -1], [-1, 0, 1], [1, -1, 0], [0, 1, -1]],
        "rates": [1. for _ in range(6)],
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
    complexation_events = data["listeners"]["complexation_listener"][
        "complexation_events"
    ]
    assert isinstance(complexation_events, list)
    # assert isinstance(complexation_events[1], list), "This is not a list."  # <-- TODO: does this need to be a list with the new structure?
    print(f"Data:\n{data}")

