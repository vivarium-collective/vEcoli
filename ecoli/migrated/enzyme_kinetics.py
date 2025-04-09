"""
====================
MIGRATED: Convenience Kinetics
====================
"""

import numpy as np
from process_bigraph import Process

from ecoli.library.kinetic_rate_laws import KineticFluxModel
from ecoli.library.schema import numpy_schema, bulk_name_to_idx, counts
from ecoli.shared.dtypes import format_bulk_state

NAME = "enzyme_kinetics"


class EnzymeKinetics(Process):
    """Michaelis-Menten-style enzyme kinetics model

    Arguments:
        initial_parameters: Configures the :term:`process` with the
            following configuration options:

            * **reactions** (:py:class:`dict`): Specifies the
              stoichiometry, reversibility, and catalysts of each
              reaction to model. For a non-reversible reaction
              :math:`A + B \\rightleftarrows 2C` catalized by an
              enzyme :math:`E`, we have the following reaction
              specification:

              .. code-block:: python

                {
                    # reaction1 is a reaction ID
                    'reaction1': {
                        'stoichiometry': {
                            # 1 mol A is consumd per mol reaction
                            ('internal', 'A'): -1,
                            ('internal', 'B'): -1,
                            # 2 mol C are produced per mol reaction
                            ('internal', 'C'): 2,
                        },
                        'is reversible': False,
                        'catalyzed by': [
                            ('internal', 'E'),
                        ],
                    }
                }

              Note that for simplicity, we assumed all the molecules
              and enzymes were in the ``internal`` port, but this is
              not necessary.
            * **kinetic_parameters** (:py:class:`dict`): Specifies
              the kinetics of the reaction by providing
              :math:`k_{cat}` and :math:`K_M` parameters for each
              enzyme. For example, let's say that for the reaction
              described above, :math:`k{cat} = 1`, :math:`K_A = 2`,
              and :math:`K_B = 3`. Then the reaction kinetics would
              be specified by:

              .. code-block:: python

                {
                    'reaction1': {
                        ('internal', 'E'): {
                            'kcat_f': 1,  # kcat for forward reaction
                            ('internal', 'A'): 2,
                            ('internal', 'B'): 3,
                        },
                    },
                }

              If the reaction were reversible, we could have
              specified ``kcat_r`` as the :math:`k_{cat}` of the
              reverse reaction.
    """

    config_schema = {
        "reactions": "tree",
        "kinetic_parameters": "tree",
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        self.reactions = self.config["reactions"]
        kinetic_parameters = self.config["kinetic_parameters"]

        # make the kinetic model
        self.kinetic_rate_laws = KineticFluxModel(self.reactions, kinetic_parameters)

        # remove "bulk" from the name
        self.molecules_ids = [
            mol_id[1] for mol_id in self.kinetic_rate_laws.molecule_ids
        ]

        self.molecules_idx = None

    # def initial_state(self):
    #     # TODO: test if this works
    #     initial_conc = config['initial_concentrations']
    #     initial_fluxes = self.next_update(
    #         initial_conc, self.parameters['time_step'])
    #     return initial_fluxes

    def inputs(self):
        return {
            "bulk": "bulk",
        }

    def outputs(self):
        return {
            "fluxes": {
                str(rxn_id): "float"
                for rxn_id in self.kinetic_rate_laws.reaction_ids
            },
        }

    def update(self, state, interval):
        bulk_state = format_bulk_state(state)
        if self.molecules_idx is None:
            bulk_ids = bulk_state["id"]
            self.molecules_idx = bulk_name_to_idx(self.molecules_ids, bulk_ids)

        # TODO (Cyrus) -- convert molecules to concentrations
        molecule_counts = counts(bulk_state, self.molecules_idx)
        tuplified_states = {
            ("bulk", mol): molecule_counts[i]
            for i, mol in enumerate(self.molecules_ids)
        }

        # get flux, which is in units of mmol / L
        fluxes = self.kinetic_rate_laws.get_fluxes(tuplified_states)

        return {"fluxes": fluxes}


def test_enzyme_kinetics(end_time=100):
    toy_reactions = {
        "reaction1": {
            "stoichiometry": {("bulk", "A"): 1, ("bulk", "B"): -1},
            "is reversible": False,
            "catalyzed by": [("bulk", "enzyme1")],
        }
    }

    toy_kinetics = {
        "reaction1": {
            ("bulk", "enzyme1"): {
                ("bulk", "B"): 0.2,
                "kcat_f": 5e1,
            }
        }
    }

    config = {
        "reactions": toy_reactions,
        "kinetic_parameters": toy_kinetics,
    }

    kinetic_process = EnzymeKinetics(config)

    initial_state = {
        "bulk": np.array(
            [("A", 1.0), ("B", 1.0), ("enzyme1", 1.0)],
            dtype=[("id", "U7"), ("count", "f")],
        )
    }
    settings = {"total_time": end_time, "initial_state": initial_state}

    # data = simulate_process(kinetic_process, settings)
    data = kinetic_process.update(initial_state, end_time)
    return data is not None


# run module with uv run ecoli/processes/enzyme_kinetics.py
