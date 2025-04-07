"""
TfUnbinding
Unbind transcription factors from DNA to allow signaling processes before
binding back to DNA.
"""

import numpy as np
import warnings

from process_bigraph import Step

from ecoli.processes.registries import topology_registry
from ecoli.library.schema import bulk_name_to_idx, attrs, numpy_schema
from ecoli.shared.dtypes import format_bulk_state


# Register default topology for this process, associating it with process name
# NAME = "ecoli-tf-unbinding"
# TOPOLOGY = {
#     "bulk": ("bulk",),
#     "promoters": (
#         "unique",
#         "promoter",
#     ),
#     "global_time": ("global_time",),
#     "timestep": ("timestep",),
#     "next_update_time": ("next_update_time", "tf_unbinding"),
# }
# topology_registry.register(NAME, TOPOLOGY)


class TfUnbinding(Step):
    """TfUnbinding"""

    config_schema = {
        "time_step": {
            "_type": "integer",
            "_default": 1,
        },
        "emit_unique": {
            "_type": "boolean",
            "_default": False,
        },
        "submass_indices": "maybe[list]",
        "active_tf_masses": "maybe[list]"
    }

    # Constructor
    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.tf_ids = self.config["tf_ids"]
        self.submass_indices = self.config.get("submass_indices", np.array([]))
        self.active_tf_masses = self.config.get("active_tf_masses", np.array([]))

        # Numpy indices for bulk molecules
        self.active_tf_idx = None

    def inputs(self):
        return {
            "bulk": "bulk",
            "promoters": "tree",
            "global_time": "global_time",
            "timestep": "float",
            "next_update_time": "float"
        }

    def outputs(self):
        return {
            "bulk": "bulk",
            "promoters": "tree",
            "global_time": "global_time",
            "timestep": "float",
            "next_update_time": "float"
        }

    def update_condition(self, state):
        """
        See :py:meth:`~.Requester.update_condition`.
        """
        if state["next_update_time"] <= state["global_time"]:
            if state["next_update_time"] < state["global_time"]:
                warnings.warn(
                    f"TfUnbinding updated at t="
                    f"{state['global_time']} instead of t="
                    f"{state['next_update_time']}. Decrease the "
                    "timestep for the global clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False

    def next_update(self, state, interval):
        bulk_state = format_bulk_state(state)
        # At t=0, convert all strings to indices
        if self.active_tf_idx is None:
            self.active_tf_idx = bulk_name_to_idx(
                [tf + "[c]" for tf in self.tf_ids], bulk_state["id"]
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
            "bulk": [(
                self.active_tf_idx.tolist() if isinstance(self.active_tf_idx, np.ndarray) else self.active_tf_idx,
                n_bound_TF
            )],
            "promoters": {
                # Reset bound_TF attribute of promoters
                "set": {"bound_TF": np.zeros_like(bound_TF).tolist()},
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
