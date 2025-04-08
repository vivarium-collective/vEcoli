"""
============================
MIGRATED: Transcription Factor Binding
============================

This process models how transcription factors bind to promoters on the DNA sequence.
"""

import numpy as np
import warnings

from process_bigraph import Step

from ecoli.library.schema import (
    listener_schema,
    numpy_schema,
    attrs,
    bulk_name_to_idx,
    counts,
)

from wholecell.utils.random import stochasticRound
from wholecell.utils import units

from ecoli.processes.registries import topology_registry


# Register default topology for this process, associating it with process name
# NAME = "ecoli-tf-binding"
# TOPOLOGY = {
#     "promoters": ("unique", "promoter"),
#     "bulk": ("bulk",),
#     "bulk_total": ("bulk",),
#     "listeners": ("listeners",),
#     "timestep": ("timestep",),
#     "next_update_time": ("next_update_time", "tf_binding"),
#     "global_time": ("global_time",),
# }
# topology_registry.register(NAME, TOPOLOGY)


class TfBinding(Step):
    """Transcription Factor Binding Step"""

    config_schema = {
        "tf_ids": "list",
        "rna_ids": "list",
        "delta_prob": "tree[list]",  # <-- this type schema should be: {"deltaI": [], "deltaJ": [], "deltaV": []},
        "n_avogadro": {
            "_default": 6.02214076e23,
            "_type": "float",
        },
        "cell_density": {
            "_default": 1100,
            "_type": "float",
        },
        # Calculate promoter binding probability when not 0CS TF
        "tf_to_tf_type": "tree",
        "active_to_bound": "tree",
        "active_to_inactive_tf": "tree",
        "bulk_molecule_ids": "list",
        "seed": "integer",
        "submass_to_idx": {
            "_type": "map[integer]",
            "_default": {
                "rRNA": 0,
                "tRNA": 1,
                "mRNA": 2,
                "miscRNA": 3,
                "nonspecific_RNA": 4,
                "protein": 5,
                "metabolite": 6,
                "water": 7,
                "DNA": 8,
            }
        },
        "emit_unique": {
            "_default": False,
            "_type": "boolean"
        },
    }

    # Constructor
    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        # Get IDs of transcription factors
        self.marR_tet_idx = None
        self.marR_idx = None
        self.inactive_tf_idx = None
        self.tf_ids = self.config["tf_ids"]
        self.n_TF = len(self.tf_ids)

        self.rna_ids = self.config["rna_ids"]

        # Build dict that maps TFs to transcription units they regulate
        self.delta_prob = self.config["delta_prob"]
        self.TF_to_TU_idx = {}

        for i, tf in enumerate(self.tf_ids):
            self.TF_to_TU_idx[tf] = self.delta_prob["deltaI"][
                self.delta_prob["deltaJ"] == i
            ]

        # Get total counts of transcription units
        self.n_TU = self.delta_prob["shape"][0]

        # Get constants
        self.n_avogadro = self.config["n_avogadro"] / units.mol
        self.cell_density = self.config["cell_density"] * units.g / units.L

        # Create dictionaries and method
        self.p_promoter_bound_tf = lambda active, inactive: float(active) / (float(active) + float(inactive))
        self.tf_to_tf_type = self.config["tf_to_tf_type"]

        self.active_to_bound = self.config["active_to_bound"]
        self.get_unbound = lambda tf: ""
        self.active_to_inactive_tf = self.config["active_to_inactive_tf"]

        self.active_tfs = {}
        self.inactive_tfs = {}

        for tf in self.tf_ids:
            self.active_tfs[tf] = tf + "[c]"

            if self.tf_to_tf_type[tf] == "1CS":
                if tf == self.active_to_bound[tf]:
                    self.inactive_tfs[tf] = self.get_unbound(tf + "[c]")
                else:
                    self.inactive_tfs[tf] = self.active_to_bound[tf] + "[c]"
            elif self.tf_to_tf_type[tf] == "2CS":
                self.inactive_tfs[tf] = self.active_to_inactive_tf[tf + "[c]"]

        self.bulk_mass_data = np.array(self.config["bulk_mass_data"]) * units.g / units.mol  # <-- should have at least 2dims

        # Build array of active TF masses
        self.bulk_molecule_ids = self.config["bulk_molecule_ids"]
        tf_indexes = [
            np.where(self.bulk_molecule_ids == tf_id + "[c]")[0][0]
            for tf_id in self.tf_ids
        ]
        self.active_tf_masses = (
            self.bulk_mass_data[tf_indexes] / self.n_avogadro
        ).asNumber(units.fg)

        self.seed = self.config["seed"]
        self.random_state = np.random.RandomState(seed=self.seed)

        # Helper indices for Numpy indexing
        self.active_tf_idx = None
        if "PD00365" in self.tf_ids:
            self.marR_name = "CPLX0-7710[c]"
            self.marR_tet = "marR-tet[c]"
        self.submass_indices = self.config["submass_indices"]

    def inputs(self):
        return {
            "promoters": "promoters",
            "bulk": "bulk",
            "bulk_total": "bulk",
            "listeners": "tree",
            "next_update_time": "float",
            "global_time": "float",
            "timestep": "float",
        }

    def outputs(self):
        return {
            "promoters": "promoters",
            "bulk": "bulk",
            "bulk_total": "bulk",
            "listeners": "tree",
            "next_update_time": "float",
            "global_time": "float",
            "timestep": "float",
        }

    def update_condition(self, state):
        if state["next_update_time"] <= state["global_time"]:
            if state["next_update_time"] < state["global_time"]:
                warnings.warn(
                    f"TfBinding updated at t="
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
            bulk_ids = state["bulk"]["id"]
            self.active_tf_idx = {
                tf_id: bulk_name_to_idx(tf_name, bulk_ids)
                for tf_id, tf_name in self.active_tfs.items()
            }
            self.inactive_tf_idx = {
                tf_id: bulk_name_to_idx(tf_name, bulk_ids)
                for tf_id, tf_name in self.inactive_tfs.items()
            }
            if "PD00365" in self.tf_ids:
                self.marR_idx = bulk_name_to_idx(self.marR_name, bulk_ids)
                self.marR_tet_idx = bulk_name_to_idx(self.marR_tet, bulk_ids)

        # If there are no promoters, return immediately
        if state["promoters"]["_entryState"].sum() == 0:
            return {"promoters": {}}

        # Get attributes of all promoters
        TU_index, bound_TF = attrs(state["promoters"], ["TU_index", "bound_TF"])

        # Calculate number of bound TFs for each TF prior to changes
        n_bound_TF = bound_TF.sum(axis=0)

        # Initialize new bound_TF array
        bound_TF_new = np.zeros_like(bound_TF)

        # Create vectors for storing values
        pPromotersBound = np.zeros(self.n_TF, dtype=np.float64)
        nPromotersBound = np.zeros(self.n_TF, dtype=int)
        nActualBound = np.zeros(self.n_TF, dtype=int)
        n_promoters = np.zeros(self.n_TF, dtype=int)
        n_bound_TF_per_TU = np.zeros((self.n_TU, self.n_TF), dtype=np.int16)

        update = {"bulk": []}

        for tf_idx, tf_id in enumerate(self.tf_ids):
            # Free all DNA-bound transcription factors into free active
            # transcription factors
            curr_tf_idx = self.active_tf_idx[tf_id]
            tf_count = counts(state["bulk"], curr_tf_idx)

            bound_tf_counts = n_bound_TF[tf_idx]
            update["bulk"].append((curr_tf_idx, bound_tf_counts))

            # Get counts of transcription factors
            active_tf_counts = (
                counts(state["bulk_total"], curr_tf_idx) + bound_tf_counts
            )
            n_available_active_tfs = tf_count + bound_tf_counts

            # NEW to vivarium-ecoli
            # Uncomplexed marR reduces active marA
            if tf_id == "PD00365":
                marR_count = counts(state["bulk_total"], self.marR_idx)
                marR_tet_count = counts(state["bulk_total"], self.marR_tet_idx)
                # marA activity ramps up as more marR is complexed off
                # TODO: Figure out how to modify ParCa so MarA/R are included
                # as active TFs so no need to compromise basal or tetracycline
                # behavior when total MarR count is zero
                ratio = marR_tet_count / max(marR_count + marR_tet_count, 1)
                # 34 = # of promoters for genes that marA regulates
                n_available_active_tfs = int(34 * ratio)

            # Determine the number of available promoter sites
            available_promoters = np.isin(TU_index, self.TF_to_TU_idx[tf_id])
            n_available_promoters = np.count_nonzero(available_promoters)
            n_promoters[tf_idx] = n_available_promoters

            # If there are no active transcription factors to work with,
            # continue to the next transcription factor
            if n_available_active_tfs == 0:
                continue

            # Compute probability of binding the promoter
            if self.tf_to_tf_type[tf_id] == "0CS":
                pPromoterBound = 1.0
            else:
                inactive_tf_counts = counts(
                    state["bulk_total"], self.inactive_tf_idx[tf_id]
                )
                pPromoterBound = self.p_promoter_bound_tf(
                    active_tf_counts, inactive_tf_counts
                )

            # Calculate the number of promoters that should be bound
            n_to_bind = int(
                min(
                    stochasticRound(
                        self.random_state,
                        np.full(n_available_promoters, pPromoterBound),
                    ).sum(),
                    n_available_active_tfs,
                )
            )

            bound_locs = np.zeros(n_available_promoters, dtype=bool)
            if n_to_bind > 0:
                # Determine randomly which DNA targets to bind based on which of
                # the following is more limiting:
                # number of promoter sites to bind, or number of active
                # transcription factors
                bound_locs[
                    self.random_state.choice(
                        n_available_promoters, size=n_to_bind, replace=False
                    )
                ] = True

                # Update count of free transcription factors
                update["bulk"].append((curr_tf_idx, -bound_locs.sum()))

                # Update bound_TF array
                bound_TF_new[available_promoters, tf_idx] = bound_locs

            n_bound_TF_per_TU[:, tf_idx] = np.bincount(
                TU_index[bound_TF_new[:, tf_idx]], minlength=self.n_TU
            )

            # Record values
            pPromotersBound[tf_idx] = pPromoterBound
            nPromotersBound[tf_idx] = n_to_bind
            nActualBound[tf_idx] = bound_locs.sum()

        delta_TF = bound_TF_new.astype(np.int8) - bound_TF.astype(np.int8)
        mass_diffs = delta_TF.dot(self.active_tf_masses)

        submass_update = {
            submass: attrs(state["promoters"], [submass])[0] + mass_diffs[:, i]
            for submass, i in self.submass_indices.items()
        }
        update["promoters"] = {"set": {"bound_TF": bound_TF_new, **submass_update}}

        update["listeners"] = {
            "rna_synth_prob": {
                "p_promoter_bound": pPromotersBound,
                "n_promoter_bound": nPromotersBound,
                "n_actual_bound": nActualBound,
                "n_available_promoters": n_promoters,
                # 900 KB, very large, comment out to halve emit size
                "n_bound_TF_per_TU": n_bound_TF_per_TU,
            },
        }

        update["next_update_time"] = state["global_time"] + state["timestep"]
        return update


def test_tf_binding_listener():
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file()
    sim.total_time = 2
    sim.raw_output = False
    sim.build_ecoli()
    sim.run()
    data = sim.query()
    assert data is not None

