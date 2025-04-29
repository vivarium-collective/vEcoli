"""
====================
MIGRATED: RNA Counts Listener
====================
"""

import numpy as np
from ecoli.library.schema import numpy_schema, attrs, listener_schema

from ecoli.shared.registry import ecoli_core
from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import collapse_defaults, get_defaults_schema


NAME = "RNA_counts_listener"
TOPOLOGY = {
    "listeners": ("listeners",),
    "RNAs": ("unique", "RNA"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
ecoli_core.topology.register(NAME, TOPOLOGY)


class RNACounts(StepBase):
    """
    Listener for the counts of each mRNA and rRNA transcription units and
        cistrons. Includes the counts of both partial and full transcripts.
    """

    name = NAME
    topology = TOPOLOGY

    defaults = {
        "rna_ids": [],
        "mrna_indexes": [],
        "time_step": 1,
        "emit_unique": False,
    }

    def initialize(self, config):
        # Get IDs and indexes of all mRNA and rRNA transcription units
        self.all_TU_ids = config["all_TU_ids"]
        self.mRNA_indexes = config["mRNA_indexes"]
        self.mRNA_TU_ids = config["mRNA_TU_ids"]
        self.rRNA_indexes = config["rRNA_indexes"]
        self.rRNA_TU_ids = config["rRNA_TU_ids"]

        # Get IDs and indexes of all mRNA and rRNA cistrons
        self.all_cistron_ids = config["all_cistron_ids"]
        self.cistron_is_mRNA = config["cistron_is_mRNA"]
        self.mRNA_cistron_ids = config["mRNA_cistron_ids"]
        self.cistron_is_rRNA = config["cistron_is_rRNA"]
        self.rRNA_cistron_ids = config["rRNA_cistron_ids"]

        # Get mapping matrix between TUs and cistrons
        self.cistron_tu_mapping_matrix = config["cistron_tu_mapping_matrix"]

        self.input_ports = {
            "RNAs": numpy_schema("RNAs"),
            "global_time": {"_default": 0.0},
            "timestep": {"_default": config["time_step"]},
        }
        self.output_ports = {
            "listeners": {
                "rna_counts": listener_schema(
                    {
                        "mRNA_counts": ([], self.mRNA_TU_ids),
                        "full_mRNA_counts": ([], self.mRNA_TU_ids),
                        "partial_mRNA_counts": ([], self.mRNA_TU_ids),
                        "mRNA_cistron_counts": ([], self.mRNA_cistron_ids),
                        "full_mRNA_cistron_counts": ([], self.mRNA_cistron_ids),
                        "partial_mRNA_cistron_counts": ([], self.mRNA_cistron_ids),
                        "partial_rRNA_counts": ([], self.rRNA_TU_ids),
                        "partial_rRNA_cistron_counts": ([], self.rRNA_cistron_ids),
                    }
                )
            }
        }

    def update_condition(self, timestep, states):
        return (states["global_time"] % states["timestep"]) == 0
    
    def inputs(self):
        return get_defaults_schema(self.input_ports)
    
    def outputs(self):
        return get_defaults_schema(self.output_ports)
    
    def initial_state(self):
        return collapse_defaults(self.output_ports)

    def update(self, state):
        # Get attributes of mRNAs
        TU_indexes, can_translate, is_full_transcript = attrs(
            state["RNAs"], ["TU_index", "can_translate", "is_full_transcript"]
        )
        is_rRNA = np.isin(TU_indexes, self.rRNA_indexes)

        # Get counts of mRNA and rRNA transcription units
        all_TU_counts = np.bincount(
            TU_indexes[np.logical_or(can_translate, is_rRNA)],
            minlength=len(self.all_TU_ids),
        )
        mRNA_counts = all_TU_counts[self.mRNA_indexes]
        full_TU_counts = np.bincount(
            TU_indexes[np.logical_and(can_translate, is_full_transcript)],
            minlength=len(self.all_TU_ids),
        )
        full_mRNA_counts = full_TU_counts[self.mRNA_indexes]
        partial_TU_counts = all_TU_counts - full_TU_counts
        partial_mRNA_counts = mRNA_counts - full_mRNA_counts
        # All unique rRNAs are partially transcribed
        partial_rRNA_counts = all_TU_counts[self.rRNA_indexes]

        # Calculate counts of mRNA cistrons from transcription unit counts
        # TODO (ggsun): Partial RNA cistron counts should take into account
        # 	the lengths of each RNA transcript.
        cistron_counts = self.cistron_tu_mapping_matrix.dot(all_TU_counts)
        mRNA_cistron_counts = cistron_counts[self.cistron_is_mRNA]
        full_mRNA_cistron_counts = self.cistron_tu_mapping_matrix.dot(full_TU_counts)[
            self.cistron_is_mRNA
        ]
        partial_mRNA_cistron_counts = self.cistron_tu_mapping_matrix.dot(
            partial_TU_counts
        )[self.cistron_is_mRNA]
        partial_rRNA_cistron_counts = cistron_counts[self.cistron_is_rRNA]

        update = {
            "listeners": {
                "rna_counts": {
                    "mRNA_counts": mRNA_counts,
                    "full_mRNA_counts": full_mRNA_counts,
                    "partial_mRNA_counts": partial_mRNA_counts,
                    "partial_rRNA_counts": partial_rRNA_counts,
                    "mRNA_cistron_counts": mRNA_cistron_counts,
                    "full_mRNA_cistron_counts": full_mRNA_cistron_counts,
                    "partial_mRNA_cistron_counts": partial_mRNA_cistron_counts,
                    "partial_rRNA_cistron_counts": partial_rRNA_cistron_counts,
                }
            }
        }
        return update


def test_rna_counts_listener():
    from ecoli.experiments.ecoli_master_sim import EcoliSim

    sim = EcoliSim.from_file()

    args = [('total_time', 2), ('raw_output', False)]
    for attr, value in args:
        setattr(sim, attr, value)

    sim.build_ecoli()
    sim.run()
    listeners = sim.query()["agents"]["0"]["listeners"]
    assert isinstance(listeners["rna_counts"]["mRNA_counts"][0], list)
    assert isinstance(listeners["rna_counts"]["mRNA_counts"][1], list)

