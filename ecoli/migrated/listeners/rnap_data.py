"""
====================
MIGRATED: RNAP Data Listener
====================
"""

import numpy as np
import warnings

from ecoli.library.schema import attrs
from ecoli.processes.transcript_elongation import get_mapping_arrays
from ecoli.shared.registry import ecoli_core
from ecoli.shared.interface import ListenerBase
from ecoli.shared.utils.schemas import listener_schema, numpy_schema


NAME = "rnap_data_listener"
TOPOLOGY = {
    "listeners": ("listeners",),
    "active_RNAPs": ("unique", "active_RNAP"),
    "RNAs": ("unique", "RNA"),
    "active_ribosomes": ("unique", "active_ribosome"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
    "next_update_time": ("next_update_time", NAME),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class RnapData(ListenerBase):
    """
    Listener for RNAP data.
    """

    name = NAME
    topology = TOPOLOGY

    defaults = {
        **ListenerBase.defaults,
        "stable_RNA_indexes": [],
        "cistron_ids": [],
        "cistron_tu_mapping_matrix": []
    }

    def initialize(self, config):
        self.stable_RNA_indexes = config["stable_RNA_indexes"]
        self.cistron_ids = config["cistron_ids"]
        self.cistron_tu_mapping_matrix = config["cistron_tu_mapping_matrix"]

        n_TUs = self.cistron_tu_mapping_matrix.shape[1]
        bidirectional_ports = {
            "listeners": {
                "rnap_data": listener_schema(
                    {
                        "rna_init_event": np.zeros(n_TUs, dtype=np.int64),
                        "active_rnap_coordinates": [],
                        "active_rnap_domain_indexes": [],
                        "active_rnap_unique_indexes": [],
                        "active_rnap_on_stable_RNA_indexes": [],
                        "active_rnap_n_bound_ribosomes": [],
                        "rna_init_event_per_cistron": (
                            [0] * len(self.cistron_ids),
                            self.cistron_ids,
                        ),
                    }
                )
            },
            "next_update_time": {
                "_default": config["time_step"]
            },
        }

        self.input_ports = {
            "next_update_time": bidirectional_ports["next_update_time"],
            "active_RNAPs": numpy_schema(
                "active_RNAPs"
            ),
            "RNAs": numpy_schema("RNAs"),
            "active_ribosomes": numpy_schema(
                "active_ribosome"
            ),
            "global_time": {"_default": 0.0},
            "timestep": {"_default": config["time_step"]},
        }
        self.output_ports = bidirectional_ports

    def update_condition(self, timestep, states):
        """
        See :py:meth:`~ecoli.processes.partition.Requester.update_condition`.
        """
        if states["next_update_time"] <= states["global_time"]:
            if states["next_update_time"] < states["global_time"]:
                warnings.warn(
                    f"{self.name} updated at t="
                    f"{states['global_time']} instead of t="
                    f"{states['next_update_time']}. Decrease the "
                    "timestep for the global clock process for more "
                    "accurate timekeeping."
                )
            return True
        return False

    def update(self, state):
        # Read coordinates of all active RNAPs
        coordinates, domain_indexes, RNAP_unique_indexes = attrs(
            state["active_RNAPs"], ["coordinates", "domain_index", "unique_index"]
        )

        (RNA_RNAP_index, is_full_transcript, RNA_unique_indexes, TU_indexes) = attrs(
            state["RNAs"],
            ["RNAP_index", "is_full_transcript", "unique_index", "TU_index"],
        )

        is_partial_transcript = np.logical_not(is_full_transcript)
        is_stable_RNA = np.isin(TU_indexes, self.stable_RNA_indexes)
        partial_RNA_RNAP_indexes = RNA_RNAP_index[is_partial_transcript]
        partial_RNA_unique_indexes = RNA_unique_indexes[is_partial_transcript]

        (ribosome_RNA_index,) = attrs(state["active_ribosomes"], ["mRNA_index"])

        RNA_index_counts = dict(zip(*np.unique(ribosome_RNA_index, return_counts=True)))

        partial_RNA_to_RNAP_mapping, _ = get_mapping_arrays(
            partial_RNA_RNAP_indexes, RNAP_unique_indexes
        )

        update = {
            "listeners": {
                "rnap_data": {
                    "active_rnap_coordinates": coordinates,
                    "active_rnap_domain_indexes": domain_indexes,
                    "active_rnap_unique_indexes": RNAP_unique_indexes,
                    "active_rnap_on_stable_RNA_indexes": RNA_RNAP_index[
                        np.logical_and(is_stable_RNA, is_partial_transcript)
                    ],
                    "active_rnap_n_bound_ribosomes": np.array(
                        [
                            RNA_index_counts.get(partial_RNA_unique_indexes[i], 0)
                            for i in partial_RNA_to_RNAP_mapping
                        ]
                    ),
                    # Calculate hypothetical RNA initiation events per cistron
                    "rna_init_event_per_cistron": self.cistron_tu_mapping_matrix.dot(
                        state["listeners"]["rnap_data"]["rna_init_event"]
                    ),
                }
            },
            "next_update_time": state["global_time"] + state["timestep"],
        }
        return update
