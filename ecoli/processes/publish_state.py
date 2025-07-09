"""
=============
PublishState Step
=============

Communicate with the NATS messaging server https://nats.io/
to send continuous simulation results back to the API
as the simulation is running
"""

import numpy as np
from numpy.lib import recfunctions as rfn

import sys
import json
import asyncio
import logging
import threading

from vivarium.core.process import Step
from ecoli.library.schema import numpy_schema, counts, attrs, bulk_name_to_idx
from ecoli.processes.registries import topology_registry
from wholecell.utils import units
from wholecell.io.simple_nats import NatsClient

# Register default topology for this step, associating it with process name
NAME = "publish-state"
TOPOLOGY = {
    "bulk": ("bulk",),
    "unique": ("unique",),
    "listeners": ("listeners",),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
topology_registry.register(NAME, TOPOLOGY)


class PublishState(Step):
    """Publish the state each timestep to a NATS server"""

    name = NAME
    topology = TOPOLOGY

    defaults = {
        "publish": {},
        "bulk_ids": [],
        "unique_ids": [],
        "emit_unique": False,
        "submass_to_idx": {
            "rRNA": 0,
            "tRNA": 1,
            "mRNA": 2,
            "miscRNA": 3,
            "nonspecific_RNA": 4,
            "protein": 5,
            "metabolite": 6,
            "water": 7,
            "DNA": 8,
        },
        "n_avogadro": 6.0221409e23,  # 1/mol
        "time_step": 1.0,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        if self.parameters['publish']:
            self.producer = NatsClient()
            self.producer.connect(
                self.parameters['publish']['address'])

        # molecule indexes and masses
        self.bulk_ids = self.parameters["bulk_ids"]
        self.unique_ids = self.parameters["unique_ids"]

        self.submass_listener_indices = {
            "rna": np.array(
                [
                    self.parameters["submass_to_idx"][name]
                    for name in ["rRNA", "tRNA", "mRNA", "miscRNA", "nonspecific_RNA"]
                ]
            ),
            "rRna": self.parameters["submass_to_idx"]["rRNA"],
            "tRna": self.parameters["submass_to_idx"]["tRNA"],
            "mRna": self.parameters["submass_to_idx"]["mRNA"],
            "dna": self.parameters["submass_to_idx"]["DNA"],
            "protein": self.parameters["submass_to_idx"]["protein"],
            "smallMolecule": self.parameters["submass_to_idx"]["metabolite"],
            "water": self.parameters["submass_to_idx"]["water"],
        }

        self.time_step = self.parameters["time_step"]

        # Helper indices for Numpy indexing
        self.bulk_idx = None

    def ports_schema(self):
        def split_divider_schema(metadata):
            return {
                "_default": 0.0,
                "_updater": "set",
                "_emit": True,
                "_divide": "split",
                "_properties": {"metadata": metadata},
            }

        set_divider_schema = {
            "_default": 0.0,
            "_updater": "set",
            "_emit": True,
            "_divide": "set",
        }

        # Ensure that bulk ids are emitted in config for analyses
        bulk_schema = numpy_schema("bulk")
        bulk_schema.setdefault("_properties", {})["metadata"] = self.bulk_ids

        ports = {
            "bulk": bulk_schema,
            "unique": {
                str(mol_id): numpy_schema(
                    mol_id + "s", emit=self.parameters["emit_unique"]
                )
                for mol_id in self.unique_ids
                if mol_id not in ["DnaA_box", "active_ribosome"]
            },
            "listeners": {
                "mass": {
                    "cell_mass": split_divider_schema("fg"),
                    "water_mass": split_divider_schema("fg"),
                    "dry_mass": split_divider_schema("fg"),
                    **{
                        submass + "_mass": split_divider_schema("fg")
                        for submass in self.submass_listener_indices.keys()
                    },
                    "volume": split_divider_schema(""),
                    "protein_mass_fraction": set_divider_schema,
                    "rna_mass_fraction": set_divider_schema,
                    "growth": set_divider_schema,
                    "instantaneous_growth_rate": set_divider_schema,
                    "dry_mass_fold_change": set_divider_schema,
                    "protein_mass_fold_change": set_divider_schema,
                    "rna_mass_fold_change": set_divider_schema,
                    "small_molecule_fold_change": set_divider_schema,
                    # compartment mass
                    "projection_mass": split_divider_schema("fg"),
                    "cytosol_mass": split_divider_schema("fg"),
                    "extracellular_mass": split_divider_schema("fg"),
                    "flagellum_mass": split_divider_schema("fg"),
                    "membrane_mass": split_divider_schema("fg"),
                    "outer_membrane_mass": split_divider_schema("fg"),
                    "periplasm_mass": split_divider_schema("fg"),
                    "pilus_mass": split_divider_schema("fg"),
                    "inner_membrane_mass": split_divider_schema("fg"),
                    "expected_mass_fold_change": split_divider_schema(""),
                }
            },
            "global_time": {"_default": 0.0},
            "timestep": {"_default": self.parameters["time_step"]},
        }
        ports["unique"].update(
            {
                "active_ribosome": numpy_schema(
                    "active_ribosome", emit=self.parameters["emit_unique"]
                ),
                "DnaA_box": numpy_schema(
                    "DnaA_boxes", emit=self.parameters["emit_unique"]
                ),
            }
        )
        return ports

    def update_condition(self, timestep, states):
        return self.parameters['publish'] and (
            states["global_time"] % states["timestep"]) == 0

    def next_update(self, timestep, states):
        if self.bulk_idx is None:
            bulk_ids = states["bulk"]["id"]
            self.bulk_idx = bulk_name_to_idx(self.bulk_ids, bulk_ids)

        dry_mass = states["listeners"]["mass"]["dry_mass"]

        bulk_counts = counts(
            states["bulk"],
            self.bulk_idx)

        update = {
            'bulk': bulk_counts.tolist()
        }

        self.producer.publish(
            'test.ecoli-publish',
            json.dumps(update).encode('utf8'))

        return {}


