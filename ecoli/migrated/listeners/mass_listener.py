"""
=============
MIGRATED: Mass Listener
=============

Represents the total cellular mass.
"""


import numpy as np
from numpy.lib import recfunctions as rfn

from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import collapse_defaults, get_defaults_schema, numpy_schema
from wholecell.utils import units
from ecoli.library.schema import counts, attrs, bulk_name_to_idx
from ecoli.shared.registry import ecoli_core



# Register default topology for this process, associating it with process name
NAME = "ecoli-mass-listener"
TOPOLOGY = {
    "bulk": ("bulk",),
    "unique": ("unique",),
    "listeners": ("listeners",),
    "global_time": ("global_time",),
    "timestep": ("timestep",),
}
ecoli_core.topology.register(NAME, TOPOLOGY)
ecoli_core.topology.register("post-division-mass-listener", TOPOLOGY)


class MassListener(StepBase):
    """MassListener"""

    name = NAME
    topology = TOPOLOGY

    defaults = {
        "cellDensity": 1100.0,
        "bulk_ids": [],
        "bulk_masses": np.zeros([1, 9]),
        "unique_ids": [],
        "unique_masses": np.zeros([1, 9]),
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
        "compartment_indices": {
            "projection": [],
            "cytosol": [],
            "extracellular": [],
            "flagellum": [],
            "membrane": [],
            "outer_membrane": [],
            "periplasm": [],
            "pilus": [],
            "inner_membrane": [],
        },
        "compartment_id_to_index": {},
        "compartment_abbrev_to_index": {},
        "n_avogadro": 6.0221409e23,  # 1/mol
        "time_step": 1.0,
        "emit_unique": False,
        "match_wcecoli": False,
    }

    def initialize(self, config):
        # molecule indexes and masses
        self.bulk_ids = config["bulk_ids"]
        self.bulk_masses = config["bulk_masses"]
        self.unique_ids = config["unique_ids"]
        self.unique_masses = config["unique_masses"]

        self.submass_listener_indices = {
            "rna": np.array(
                [
                    config["submass_to_idx"][name]
                    for name in ["rRNA", "tRNA", "mRNA", "miscRNA", "nonspecific_RNA"]
                ]
            ),
            "rRna": config["submass_to_idx"]["rRNA"],
            "tRna": config["submass_to_idx"]["tRNA"],
            "mRna": config["submass_to_idx"]["mRNA"],
            "dna": config["submass_to_idx"]["DNA"],
            "protein": config["submass_to_idx"]["protein"],
            "smallMolecule": config["submass_to_idx"]["metabolite"],
            "water": config["submass_to_idx"]["water"],
        }
        self.ordered_submasses = [0] * len(config["submass_to_idx"])
        for submass, idx in config["submass_to_idx"].items():
            self.ordered_submasses[idx] = f"{submass}_submass"

        # compartment indexes
        self.compartment_id_to_index = config["compartment_id_to_index"]
        self.projection_index = config["compartment_indices"]["projection"]
        self.cytosol_index = config["compartment_indices"]["cytosol"]
        self.extracellular_index = config["compartment_indices"][
            "extracellular"
        ]
        self.flagellum_index = config["compartment_indices"]["flagellum"]
        self.membrane_index = config["compartment_indices"]["membrane"]
        self.outer_membrane_index = config["compartment_indices"][
            "outer_membrane"
        ]
        self.periplasm_index = config["compartment_indices"]["periplasm"]
        self.pilus_index = config["compartment_indices"]["pilus"]
        self.inner_membrane_index = config["compartment_indices"][
            "inner_membrane"
        ]

        # Set up matrix for compartment mass calculation
        self.compartment_abbrev_to_index = config[
            "compartment_abbrev_to_index"
        ]
        if self.compartment_abbrev_to_index:
            self._bulk_molecule_by_compartment = np.stack(
                [
                    np.core.defchararray.chararray.endswith(self.bulk_ids, abbrev + "]")
                    for abbrev in self.compartment_abbrev_to_index
                ]
            )

        # units and constants
        self.cellDensity = config["cellDensity"]
        self.n_avogadro = config["n_avogadro"]

        self.time_step = config["time_step"]
        self.first_time_step = True

        self.massDiff_names = [
            "massDiff_" + submass for submass in config["submass_to_idx"]
        ]

        self.cell_cycle_len = config["condition_to_doubling_time"][
            config["condition"]
        ].asNumber(units.s)

        # Helper indices for Numpy indexing
        self.bulk_idx = None

        # Enable flag for perfect recapitulation of wcEcoli mass calculations
        self.match_wcecoli = config["match_wcecoli"]

        def split_divider_port(metadata):
            return {
                "_default": 0.0,
                "_description": {"metadata": metadata}
            }

        set_divider_schema = {
            "_default": 0.0
        }

        # Ensure that bulk ids are emitted in config for analyses
        bulk_schema = numpy_schema("bulk")
        bulk_schema.setdefault("_description", {})["metadata"] = self.bulk_ids

        listeners_port = {
            "listeners": {
                "mass": {
                    "cell_mass": split_divider_port("fg"),
                    "water_mass": split_divider_port("fg"),
                    "dry_mass": split_divider_port("fg"),
                    **{
                        submass + "_mass": split_divider_port("fg")
                        for submass in self.submass_listener_indices.keys()
                    },
                    "volume": split_divider_port(""),
                    "protein_mass_fraction": set_divider_schema,
                    "rna_mass_fraction": set_divider_schema,
                    "growth": set_divider_schema,
                    "instantaneous_growth_rate": set_divider_schema,
                    "dry_mass_fold_change": set_divider_schema,
                    "protein_mass_fold_change": set_divider_schema,
                    "rna_mass_fold_change": set_divider_schema,
                    "small_molecule_fold_change": set_divider_schema,
                    # compartment mass
                    "projection_mass": split_divider_port("fg"),
                    "cytosol_mass": split_divider_port("fg"),
                    "extracellular_mass": split_divider_port("fg"),
                    "flagellum_mass": split_divider_port("fg"),
                    "membrane_mass": split_divider_port("fg"),
                    "outer_membrane_mass": split_divider_port("fg"),
                    "periplasm_mass": split_divider_port("fg"),
                    "pilus_mass": split_divider_port("fg"),
                    "inner_membrane_mass": split_divider_port("fg"),
                    "expected_mass_fold_change": split_divider_port(""),
                }
            }
        }

        self.input_ports = {
            **listeners_port,
            "bulk": bulk_schema,
            "unique": {
                str(mol_id): numpy_schema(mol_id + "s")
                for mol_id in self.unique_ids
                if mol_id not in ["DnaA_box", "active_ribosome"]
            },
            "global_time": {"_default": 0.0},
            "timestep": {"_default": config["time_step"]},
        }
        self.input_ports["unique"].update(
            {
                "active_ribosome": numpy_schema("active_ribosome"),
                "DnaA_box": numpy_schema("DnaA_boxes"),
            }
        )
        self.output_ports = listeners_port

    def update_condition(self, timestep, states):
        return (states["global_time"] % states["timestep"]) == 0

    def inputs(self):
        return get_defaults_schema(self.input_ports)

    def outputs(self):
        return get_defaults_schema(self.output_ports)

    def initial_state(self):
        return collapse_defaults(self.output_ports)
     
    def update(self, state):
        if self.bulk_idx is None:
            bulk_ids = state["bulk"]["id"]
            self.bulk_idx = bulk_name_to_idx(self.bulk_ids, bulk_ids)
            if self.match_wcecoli:
                self.bulk_addon = np.zeros((len(self.bulk_idx), 16))

        mass_update = {}

        # Get previous dry mass, for calculating growth later
        old_dry_mass = state["listeners"]["mass"]["dry_mass"]

        # get submasses from bulk and unique
        bulk_counts = counts(state["bulk"], self.bulk_idx)
        bulk_masses = state["bulk"][self.ordered_submasses][self.bulk_idx]
        bulk_masses = rfn.structured_to_unstructured(bulk_masses)
        bulk_submasses = np.dot(bulk_counts, bulk_masses)
        bulk_compartment_masses = np.dot(
            bulk_counts * self._bulk_molecule_by_compartment, bulk_masses
        )
        if self.match_wcecoli:
            bulk_counts = np.hstack(
                [self.bulk_addon, counts(state["bulk"], self.bulk_idx)[:, np.newaxis]]
            )
            bulk_submasses = np.dot(bulk_counts.T, bulk_masses).sum(axis=0)
            bulk_compartment_masses = np.dot(
                bulk_counts.sum(axis=1) * self._bulk_molecule_by_compartment,
                bulk_masses,
            )

        unique_submasses = np.zeros(len(self.massDiff_names))
        unique_compartment_masses = np.zeros_like(bulk_compartment_masses)
        for unique_id, unique_mass in zip(self.unique_ids, self.unique_masses):
            molecules = state["unique"].get(unique_id)
            n_molecules = molecules["_entryState"].sum()

            if n_molecules == 0:
                continue

            unique_submasses += unique_mass * n_molecules
            unique_compartment_masses[self.compartment_abbrev_to_index["c"], :] += (
                unique_mass * n_molecules
            )

            massDiffs = np.array(list(attrs(molecules, self.massDiff_names))).T
            if self.match_wcecoli:
                massDiffs = np.core.records.fromarrays(
                    attrs(molecules, self.massDiff_names)
                ).view((np.float64, len(self.massDiff_names)))
            unique_submasses += massDiffs.sum(axis=0)
            unique_compartment_masses[self.compartment_abbrev_to_index["c"], :] += (
                massDiffs.sum(axis=0)
            )

        # all of the submasses
        all_submasses = bulk_submasses + unique_submasses

        # save cell mass, water mass, dry mass
        mass_update["cell_mass"] = all_submasses.sum()
        mass_update["water_mass"] = all_submasses[
            self.submass_listener_indices["water"]
        ]
        mass_update["dry_mass"] = mass_update["cell_mass"] - mass_update["water_mass"]

        # Store submasses
        for submass, indices in self.submass_listener_indices.items():
            mass_update[submass + "_mass"] = all_submasses[indices].sum()

        mass_update["volume"] = mass_update["cell_mass"] / self.cellDensity

        if self.first_time_step:
            mass_update["growth"] = 0.0
            self.dryMassInitial = mass_update["dry_mass"]
            self.proteinMassInitial = mass_update["protein_mass"]
            self.rnaMassInitial = mass_update["rna_mass"]
            self.smallMoleculeMassInitial = mass_update["smallMolecule_mass"]
            self.timeInitial = state["global_time"]
        else:
            mass_update["growth"] = mass_update["dry_mass"] - old_dry_mass

        # Compartment submasses
        compartment_submasses = bulk_compartment_masses + unique_compartment_masses
        mass_update["projection_mass"] = compartment_submasses[
            self.projection_index, :
        ].sum()
        mass_update["cytosol_mass"] = compartment_submasses[self.cytosol_index, :].sum()
        mass_update["extracellular_mass"] = compartment_submasses[
            self.extracellular_index, :
        ].sum()
        mass_update["flagellum_mass"] = compartment_submasses[
            self.flagellum_index, :
        ].sum()
        mass_update["membrane_mass"] = compartment_submasses[
            self.membrane_index, :
        ].sum()
        mass_update["outer_membrane_mass"] = compartment_submasses[
            self.outer_membrane_index, :
        ].sum()
        mass_update["periplasm_mass"] = compartment_submasses[
            self.periplasm_index, :
        ].sum()
        mass_update["pilus_mass"] = compartment_submasses[self.pilus_index, :].sum()
        mass_update["inner_membrane_mass"] = compartment_submasses[
            self.inner_membrane_index, :
        ].sum()

        # This listener tracks the mass changes caused by each process
        # TODO: Blame processes?
        # mass_update['processMassDifferences'] = sum(
        #     state.process_mass_diffs() for state in self.internal_states.values()
        # ).sum(axis=1)

        if mass_update["dry_mass"] != 0:
            mass_update["protein_mass_fraction"] = (
                mass_update["protein_mass"] / mass_update["dry_mass"]
            )
            mass_update["rna_mass_fraction"] = (
                mass_update["rna_mass"] / mass_update["dry_mass"]
            )
            mass_update["instantaneous_growth_rate"] = (
                mass_update["growth"] / self.time_step / mass_update["dry_mass"]
            )
            mass_update["dry_mass_fold_change"] = (
                mass_update["dry_mass"] / self.dryMassInitial
            )
            mass_update["protein_mass_fold_change"] = (
                mass_update["protein_mass"] / self.proteinMassInitial
            )
            mass_update["rna_mass_fold_change"] = (
                mass_update["rna_mass"] / self.rnaMassInitial
            )
            mass_update["small_molecule_fold_change"] = (
                mass_update["smallMolecule_mass"] / self.smallMoleculeMassInitial
            )
            mass_update["expected_mass_fold_change"] = np.exp(
                np.log(2)
                * (state["global_time"] - self.timeInitial)
                / self.cell_cycle_len
            )

        self.first_time_step = False

        update = {"listeners": {"mass": mass_update}}
        return update


class PostDivisionMassListener(MassListener):
    """
    Normally, the mass listener updates after all other processes and steps
    have run. However, after division, the mass must be updated immediately
    so other processes have access to the accurate mass of their daughter
    cell. This process ensures that the mass seen by other processes following
    division is accurate.
    """

    name = "post-division-mass-listener"

    def update_condition(self, timestep, states):
        return self.first_time_step
