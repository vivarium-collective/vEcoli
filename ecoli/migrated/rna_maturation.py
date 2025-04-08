"""
MIGRATED: RnaMaturation process
=====================
- Converts unprocessed tRNA/rRNA molecules into mature tRNA/rRNAs
- Consolidates the different variants of 23S, 16S, and 5S rRNAs into the single
  variant that is used for ribosomal subunits
"""

import numpy as np

from ecoli.library.schema import counts, bulk_name_to_idx
from ecoli.migrated.partition import PartitionedProcess
from ecoli.shared.dtypes import format_bulk_state, format_state, bulk_dtype


# Register default topology for this process, associating it with process name
# NAME = "ecoli-rna-maturation"
# TOPOLOGY = {"bulk": ("bulk",), "bulk_total": ("bulk",), "listeners": ("listeners",)}
# topology_registry.register(NAME, TOPOLOGY)


class RnaMaturation(PartitionedProcess):
    """RnaMaturation"""
    config_schema = {
        "stoich_matrix": "list",
        "enzyme_matrix": "list",
        "n_required_enzymes": "integer",
        "degraded_nt_counts": "integer",
        "n_ppi_added": "integer",
        "main_23s_rRNA_id": "string",
        "main_16s_rRNA_id": "string",
        "main_5s_rRNA_id": "string",
        "variant_23s_rRNA_ids": "string",
        "variant_16s_rRNA_ids": "string",
        "variant_5s_rRNA_ids": "string",
        "delta_nt_counts_23s": "string",
        "delta_nt_counts_16s": "string",
        "delta_nt_counts_5s": "string",
        "unprocessed_rna_ids": "list[string]",
        "mature_rna_ids": "list[string]",
        "rna_maturation_enzyme_ids": "list[string]",
        "ppi": "list",
        "water": "list",
        "nmps": "list",
        "proton": "list"
    }

    # Constructor
    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        
        # Get matrices and vectors that describe maturation reactions
        self.stoich_matrix = self.config["stoich_matrix"]
        self.enzyme_matrix = self.config["enzyme_matrix"]
        self.n_required_enzymes = self.config["n_required_enzymes"]
        self.degraded_nt_counts = self.config["degraded_nt_counts"]
        self.n_ppi_added = self.config["n_ppi_added"]

        # Calculate number of NMPs that should be added when consolidating rRNA
        # molecules
        self.main_23s_rRNA_id = self.config["main_23s_rRNA_id"]
        self.main_16s_rRNA_id = self.config["main_16s_rRNA_id"]
        self.main_5s_rRNA_id = self.config["main_5s_rRNA_id"]

        self.variant_23s_rRNA_ids = self.config["variant_23s_rRNA_ids"]
        self.variant_16s_rRNA_ids = self.config["variant_16s_rRNA_ids"]
        self.variant_5s_rRNA_ids = self.config["variant_5s_rRNA_ids"]

        self.delta_nt_counts_23s = self.config["delta_nt_counts_23s"]
        self.delta_nt_counts_16s = self.config["delta_nt_counts_16s"]
        self.delta_nt_counts_5s = self.config["delta_nt_counts_5s"]

        # Bulk molecule IDs
        self.unprocessed_rna_ids = self.config["unprocessed_rna_ids"]
        self.mature_rna_ids = self.config["mature_rna_ids"]
        self.rna_maturation_enzyme_ids = self.config["rna_maturation_enzyme_ids"]
        self.fragment_bases = self.config["fragment_bases"]
        self.ppi = self.config["ppi"]
        self.water = self.config["water"]
        self.nmps = self.config["nmps"]
        self.proton = self.config["proton"]

        # Numpy indices for bulk molecules
        self.ppi_idx = None

    def inputs(self):
        return {
            "bulk": "bulk",  # numpy_schema("bulk"),
            "bulk_total": "bulk",  # numpy_schema("bulk"),
            "listeners": "tree"
            # "listeners": {
            #     "rna_maturation_listener": listener_schema(
            #         {
            #             "total_maturation_events": 0,
            #             "total_degraded_ntps": 0,
            #             "unprocessed_rnas_consumed": (
            #                 [0] * len(self.unprocessed_rna_ids),
            #                 self.unprocessed_rna_ids,
            #             ),
            #             "mature_rnas_generated": (
            #                 [0] * len(self.mature_rna_ids),
            #                 self.mature_rna_ids,
            #             ),
            #             "maturation_enzyme_counts": (
            #                 [0] * len(self.rna_maturation_enzyme_ids),
            #                 self.rna_maturation_enzyme_ids,
            #             ),
            #         }
            #     )
            # },
        }

    def outputs(self):
        return {
            "bulk": "bulk",  # numpy_schema("bulk"),
            "bulk_total": "bulk",  # numpy_schema("bulk"),
            "listeners": "tree"
        }

    def calculate_request(self, state):
        # Get bulk indices
        bulk_state = format_bulk_state(state)
        bulk_total_state = format_state(state, "bulk_total", bulk_dtype)
        if self.ppi_idx is None:
            bulk_ids = bulk_state["id"]
            self.unprocessed_rna_idx = bulk_name_to_idx(
                self.unprocessed_rna_ids, bulk_ids
            )
            self.mature_rna_idx = bulk_name_to_idx(self.mature_rna_ids, bulk_ids)
            self.rna_maturation_enzyme_idx = bulk_name_to_idx(
                self.rna_maturation_enzyme_ids, bulk_ids
            )
            self.fragment_base_idx = bulk_name_to_idx(self.fragment_bases, bulk_ids)
            self.ppi_idx = bulk_name_to_idx(self.ppi, bulk_ids)
            self.water_idx = bulk_name_to_idx(self.water, bulk_ids)
            self.nmps_idx = bulk_name_to_idx(self.nmps, bulk_ids)
            self.proton_idx = bulk_name_to_idx(self.proton, bulk_ids)
            self.main_23s_rRNA_idx = bulk_name_to_idx(self.main_23s_rRNA_id, bulk_ids)
            self.main_16s_rRNA_idx = bulk_name_to_idx(self.main_16s_rRNA_id, bulk_ids)
            self.main_5s_rRNA_idx = bulk_name_to_idx(self.main_5s_rRNA_id, bulk_ids)
            self.variant_23s_rRNA_idx = bulk_name_to_idx(
                self.variant_23s_rRNA_ids, bulk_ids
            )
            self.variant_16s_rRNA_idx = bulk_name_to_idx(
                self.variant_16s_rRNA_ids, bulk_ids
            )
            self.variant_5s_rRNA_idx = bulk_name_to_idx(
                self.variant_5s_rRNA_ids, bulk_ids
            )

        unprocessed_rna_counts = counts(bulk_total_state, self.unprocessed_rna_idx)
        variant_23s_rRNA_counts = counts(
            bulk_total_state, self.variant_23s_rRNA_idx
        )
        variant_16s_rRNA_counts = counts(
            bulk_total_state, self.variant_16s_rRNA_idx
        )
        variant_5s_rRNA_counts = counts(bulk_total_state, self.variant_5s_rRNA_idx)
        self.enzyme_availability = counts(
            bulk_total_state, self.rna_maturation_enzyme_idx
        ).astype(bool)

        # Determine which maturation reactions to turn off based on enzyme
        # availability
        reaction_is_off = (
            self.enzyme_matrix.dot(self.enzyme_availability) < self.n_required_enzymes
        )
        unprocessed_rna_counts[reaction_is_off] = 0

        # Calculate NMPs, water, and proton needed to balance mass
        n_added_bases_from_maturation = np.dot(
            self.degraded_nt_counts.T, unprocessed_rna_counts
        )
        n_added_bases_from_consolidation = (
            self.delta_nt_counts_23s.T.dot(variant_23s_rRNA_counts)
            + self.delta_nt_counts_16s.T.dot(variant_16s_rRNA_counts)
            + self.delta_nt_counts_5s.T.dot(variant_5s_rRNA_counts)
        )
        n_added_bases = n_added_bases_from_maturation + n_added_bases_from_consolidation
        n_total_added_bases = int(n_added_bases.sum())

        # Request all unprocessed RNAs, ppis that need to be added to the
        # 5'-ends of mature RNAs, all variant rRNAs, and NMPs/water/protons
        # needed to balance mass
        request = {
            "bulk": [
                (self.unprocessed_rna_idx, unprocessed_rna_counts),
                (self.ppi_idx, self.n_ppi_added.dot(unprocessed_rna_counts)),
                (self.variant_23s_rRNA_idx, variant_23s_rRNA_counts),
                (self.variant_16s_rRNA_idx, variant_16s_rRNA_counts),
                (self.variant_5s_rRNA_idx, variant_5s_rRNA_counts),
                (self.nmps_idx, np.abs(-n_added_bases).astype(int)),
            ]
        }

        if n_total_added_bases > 0:
            request["bulk"].append((self.water_idx, n_total_added_bases))
        else:
            request["bulk"].append((self.proton_idx, -n_total_added_bases))

        return request

    def update(self, state, interval):
        # Create copy of bulk counts so can update in real-time
        bulk_state = format_bulk_state(state)
        state["bulk"] = counts(
            bulk_state,
            np.array([n for n in range(len(state["bulk"]))])
        )

        # Get counts of unprocessed RNAs
        unprocessed_rna_counts = counts(state["bulk"], self.unprocessed_rna_idx)

        # Calculate numbers of mature RNAs and fragment bases that are generated
        # upon maturation
        n_mature_rnas = self.stoich_matrix.dot(unprocessed_rna_counts)
        n_added_bases_from_maturation = np.dot(
            self.degraded_nt_counts.T, unprocessed_rna_counts
        )

        state["bulk"][self.mature_rna_idx] += n_mature_rnas
        state["bulk"][self.unprocessed_rna_idx] += -unprocessed_rna_counts
        ppi_update = self.n_ppi_added.dot(unprocessed_rna_counts)
        state["bulk"][self.ppi_idx] += -ppi_update
        update = {
            "bulk": [
                (self.mature_rna_idx, n_mature_rnas),
                (self.unprocessed_rna_idx, -unprocessed_rna_counts),
                (self.ppi_idx, -ppi_update),
            ],
            "listeners": {
                "rna_maturation_listener": {
                    "total_maturation_events": unprocessed_rna_counts.sum(),
                    "total_degraded_ntps": n_added_bases_from_maturation.sum(dtype=int),
                    "unprocessed_rnas_consumed": unprocessed_rna_counts,
                    "mature_rnas_generated": n_mature_rnas,
                    "maturation_enzyme_counts": counts(
                        state["bulk_total"], self.rna_maturation_enzyme_idx
                    ).tolist(),
                }
            },
        }

        # Get counts of variant rRNAs
        variant_23s_rRNA_counts = counts(state["bulk"], self.variant_23s_rRNA_idx)
        variant_16s_rRNA_counts = counts(state["bulk"], self.variant_16s_rRNA_idx)
        variant_5s_rRNA_counts = counts(state["bulk"], self.variant_5s_rRNA_idx)

        # Calculate number of NMPs that should be added to balance out the mass
        # difference during the consolidation
        n_added_bases_from_consolidation = (
            self.delta_nt_counts_23s.T.dot(variant_23s_rRNA_counts)
            + self.delta_nt_counts_16s.T.dot(variant_16s_rRNA_counts)
            + self.delta_nt_counts_5s.T.dot(variant_5s_rRNA_counts)
        )

        # Evolve states
        update["bulk"].extend(
            [
                (self.main_23s_rRNA_idx, variant_23s_rRNA_counts.sum()),
                (self.main_16s_rRNA_idx, variant_16s_rRNA_counts.sum()),
                (self.main_5s_rRNA_idx, variant_5s_rRNA_counts.sum()),
                (self.variant_23s_rRNA_idx, -variant_23s_rRNA_counts),
                (self.variant_16s_rRNA_idx, -variant_16s_rRNA_counts),
                (self.variant_5s_rRNA_idx, -variant_5s_rRNA_counts),
            ]
        )

        # Consume or add NMPs to balance out mass
        n_added_bases = (
            n_added_bases_from_maturation + n_added_bases_from_consolidation
        ).astype(int)
        n_total_added_bases = n_added_bases.sum()

        update["bulk"].extend(
            [
                (self.nmps_idx, n_added_bases),
                (self.water_idx, -n_total_added_bases),
                (self.proton_idx, n_total_added_bases),
            ]
        )

        return update
