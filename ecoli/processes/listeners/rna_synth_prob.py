"""
=====================
RnaSynthProb Listener
=====================
"""

import numpy as np
from ecoli.library.schema import numpy_schema, listener_schema, attrs
from ecoli.library.schema_types import PROMOTER_ARRAY, GENE_ARRAY
from ecoli.library.ecoli_step import EcoliStep as Step

from ecoli.processes.registries import topology_registry


NAME = "rna_synth_prob_listener"
TOPOLOGY = {
    "rna_synth_prob": ("listeners", "rna_synth_prob"),
    "promoters": ("unique", "promoter"),
    "genes": ("unique", "gene"),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
topology_registry.register(NAME, TOPOLOGY)


class RnaSynthProb(Step):
    """
    Listener for additional RNA synthesis data.
    """

    name = NAME
    topology = TOPOLOGY

    config_schema = {
        'time_step': 'float{1.0}',
        'emit_unique': 'boolean{false}',
        'rna_ids': 'list[string]',
        'gene_ids': 'list[string]',
        'tf_ids': 'list[string]',
        'cistron_ids': 'list[string]',
        'cistron_tu_mapping_matrix': 'csr_matrix',
    }


    def inputs(self):
        return {
            'rna_synth_prob': {
                'actual_rna_synth_prob': f'array[{self.n_TU},float]',
                'target_rna_synth_prob': f'array[{self.n_TU},float]',
                'n_bound_TF_per_TU': f'array[({self.n_TU}|{self.n_TF}),integer]',
                'total_rna_init': 'integer',
            },
            'promoters': PROMOTER_ARRAY,
            'genes': GENE_ARRAY,
            'global_time': 'float',
            'timestep': 'float',
        }

    def outputs(self):
        return {
            'rna_synth_prob': {
                'promoter_copy_number': f'array[{self.n_TU},integer]',
                'gene_copy_number': f'array[{self.n_cistron},integer]',
                'bound_TF_indexes': 'array[integer]',
                'bound_TF_coordinates': 'array[integer]',
                'bound_TF_domains': 'array[integer]',
                # Probabilities — dimensionless floats in [0, 1]
                'expected_rna_init_per_cistron': f'array[{self.n_cistron},float]',
                'actual_rna_synth_prob_per_cistron': f'array[{self.n_cistron},float]',
                'target_rna_synth_prob_per_cistron': f'array[{self.n_cistron},float]',
                'n_bound_TF_per_cistron': f'array[{self.n_cistron},integer]',
            },
        }


    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.rna_ids = self.parameters["rna_ids"]
        self.gene_ids = self.parameters["gene_ids"]
        self.tf_ids = self.parameters["tf_ids"]
        self.cistron_ids = self.parameters["cistron_ids"]
        self.n_TU = len(self.rna_ids)
        self.n_TF = len(self.tf_ids)
        self.n_cistron = len(self.cistron_ids)
        self.cistron_tu_mapping_matrix = self.parameters["cistron_tu_mapping_matrix"]

    def ports_schema(self):
        return {
            "rna_synth_prob": listener_schema(
                {
                    "promoter_copy_number": ([0] * self.n_TU, self.rna_ids),
                    "gene_copy_number": ([0] * self.n_cistron, self.cistron_ids),
                    "bound_TF_indexes": ([], self.tf_ids),
                    "bound_TF_coordinates": [],
                    "bound_TF_domains": [],
                    "target_rna_synth_prob": ([0.0] * self.n_TU, self.rna_ids),
                    "actual_rna_synth_prob": ([0.0] * self.n_TU, self.rna_ids),
                    "actual_rna_synth_prob_per_cistron": (
                        [0.0] * self.n_cistron,
                        self.cistron_ids,
                    ),
                    "target_rna_synth_prob_per_cistron": (
                        [0.0] * self.n_cistron,
                        self.cistron_ids,
                    ),
                    "expected_rna_init_per_cistron": (
                        [0.0] * self.n_cistron,
                        self.cistron_ids,
                    ),
                    "n_bound_TF_per_TU": ([[0] * self.n_TF] * self.n_TU, self.rna_ids),
                    "n_bound_TF_per_cistron": ([], self.cistron_ids),
                    "total_rna_init": 0,
                }
            ),
            "promoters": numpy_schema("promoters", emit=self.parameters["emit_unique"]),
            "genes": numpy_schema("genes", emit=self.parameters["emit_unique"]),
            "global_time": {"_default": 0.0},
            "timestep": {"_default": self.parameters["time_step"]},
        }

    def update_condition(self, timestep, states):
        return (states["global_time"] % states["timestep"]) == 0

    def update(self, states, interval=None):
        TU_indexes, all_coordinates, all_domains, bound_TFs = attrs(
            states["promoters"], ["TU_index", "coordinates", "domain_index", "bound_TF"]
        )
        bound_promoter_indexes, TF_indexes = np.where(bound_TFs)
        (cistron_indexes,) = attrs(states["genes"], ["cistron_index"])

        actual_rna_synth_prob_per_cistron = self.cistron_tu_mapping_matrix.dot(
            states["rna_synth_prob"]["actual_rna_synth_prob"]
        )
        # The expected value of rna initiations per cistron. Realized values
        # during simulation will be different, because they will be integers
        # drawn from a multinomial distribution
        expected_rna_init_per_cistron = (
            actual_rna_synth_prob_per_cistron
            * states["rna_synth_prob"]["total_rna_init"]
        )

        if actual_rna_synth_prob_per_cistron.sum() != 0:
            actual_rna_synth_prob_per_cistron = (
                actual_rna_synth_prob_per_cistron
                / actual_rna_synth_prob_per_cistron.sum()
            )
        target_rna_synth_prob_per_cistron = self.cistron_tu_mapping_matrix.dot(
            states["rna_synth_prob"]["target_rna_synth_prob"]
        )
        if target_rna_synth_prob_per_cistron.sum() != 0:
            target_rna_synth_prob_per_cistron = (
                target_rna_synth_prob_per_cistron
                / target_rna_synth_prob_per_cistron.sum()
            )

        return {
            "rna_synth_prob": {
                "promoter_copy_number": np.bincount(TU_indexes, minlength=self.n_TU),
                "gene_copy_number": np.bincount(
                    cistron_indexes, minlength=self.n_cistron
                ),
                "bound_TF_indexes": TF_indexes,
                "bound_TF_coordinates": all_coordinates[bound_promoter_indexes],
                "bound_TF_domains": all_domains[bound_promoter_indexes],
                "expected_rna_init_per_cistron": expected_rna_init_per_cistron,
                "actual_rna_synth_prob_per_cistron": actual_rna_synth_prob_per_cistron,
                "target_rna_synth_prob_per_cistron": target_rna_synth_prob_per_cistron,
                "n_bound_TF_per_cistron": self.cistron_tu_mapping_matrix.dot(
                    states["rna_synth_prob"]["n_bound_TF_per_TU"]
                )
                .astype(np.int16)
                .T,
            }
        }
