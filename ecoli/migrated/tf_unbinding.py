"""
MIGRATED: TfUnbinding
Unbind transcription factors from DNA to allow signaling processes before
binding back to DNA.
"""

import numpy as np
import warnings

from ecoli.shared.registry import ecoli_core
from ecoli.library.schema import bulk_name_to_idx, attrs
from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import numpy_schema

# Register default topology for this process, associating it with process name
NAME = "ecoli-tf-unbinding"
TOPOLOGY = {
    "bulk": ("bulk",),
    "promoters": (
        "unique",
        "promoter",
    ),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", "tf_unbinding"),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class TfUnbinding(StepBase):
    """TfUnbinding"""

    name = NAME
    defaults = {"time_step": 1, "emit_unique": False}

    # Constructor
    def __init__(self, config=None, core=None):
        super().__init__(config)
        self.tf_ids = self.config["tf_ids"]
        self.submass_indices = self.config["submass_indices"]
        self.active_tf_masses = self.config["active_tf_masses"]

        # Numpy indices for bulk molecules
        self.active_tf_idx = None

    def inputs(self):
        return {
            "bulk": numpy_schema("bulk"),
            "promoters": numpy_schema("promoters"),
            "global_time": "float",
            "timestep": self.timestep_schema,
            "next_update_time": self.timestep_schema
        }
    
    def outputs(self):
        return {
            "bulk": numpy_schema("bulk"),
            "promoters": numpy_schema("promoters"),
            "next_update_time": self.timestep_schema
        }

    def update_condition(self, state):
        """
        See :py:meth:`~.Requester.update_condition`.
        """
        if state["next_update_time"] <= state["global_time"]:
            if state["next_update_time"] < state["global_time"]:
                warnings.warn(
                    f"{self.name} updated at t="
                    f"{state['global_time']} instead of t="
                    f"{state['next_update_time']}. Decrease the "
                    "timestep for the global clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False

    def update(self, state):
        # At t=0, convert all strings to indices
        if self.active_tf_idx is None:
            self.active_tf_idx = bulk_name_to_idx(
                [tf + "[c]" for tf in self.tf_ids], state["bulk"]["id"]
            )

        # Get attributes of all promoters
        (bound_TF,) = attrs(state["promoters"], ["bound_TF"])
        # If there are no promoters, return immediately
        if len(bound_TF) == 0:
            return {}

        # Calculate number of bound TFs for each TF prior to changes
        n_bound_TF = bound_TF.sum(axis=0)

        update = {
            # Free all DNA-bound TFs into free active TFs
            "bulk": [(self.active_tf_idx, n_bound_TF)],
            "promoters": {
                # Reset bound_TF attribute of promoters
                "set": {"bound_TF": np.zeros_like(bound_TF)}
            },
        }

        # Add mass_diffs array to promoter submass
        mass_diffs = bound_TF @ -self.active_tf_masses
        for submass, idx in self.submass_indices.items():
            update["promoters"]["set"][submass] = (
                attrs(state["promoters"], [submass])[0] + mass_diffs[:, idx]
            )

        update["next_update_time"] = state["global_time"] + state["timestep"]
        return update
