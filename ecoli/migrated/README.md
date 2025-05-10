## `ecoli.migrated`: subpackage whose structure mirrors `ecoli.processes` containing process-bigraph-compliant process definitions.


### _*RE: Partitioned Processes*_:
Consider the `Complexation` process, which cleanly implements the migrated (new)`PartitionedProcess` interface as such:

```python

NAME = "ecoli-complexation"
TOPOLOGY = {"bulk": ("bulk",), "listeners": ("listeners",), "timestep": ("timestep",)}


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
```


### _*RE: Units*_:
The pre-migration version of this project/model used `vivarium.core.library.units` whereas the migrated version (process bigraph), uses `bigraph_schema.units.units`.