"""
=============
Cell Division
=============
"""

from typing import Any, Dict

import binascii
import os
import numpy as np
from ecoli.library.bigraph_bridge import BigraphProcess as Process, BigraphStep as Step

from ecoli.library.sim_data import RAND_MAX
from ecoli.library.schema import attrs
from wholecell.utils import units

NAME = "ecoli-cell-division"


def daughter_phylogeny_id(mother_id):
    return [str(mother_id) + "0", str(mother_id) + "1"]


class MarkDPeriod(Step):
    """Set division flag after D period has elapsed"""

    name = "mark_d_period"

    config_schema = {}

    def inputs(self):
        return {
            'full_chromosome': 'unique_array',
            'global_time': 'float',
        }

    def outputs(self):
        return {
            'full_chromosome': 'unique_array',
            # divide_reset[boolean] mirrors v1's ``_divider:
            # {set_value: False}`` so daughters start with divide=False
            # rather than inheriting the mother's True from the moment
            # mark_d_period triggered division. Without this the next
            # generation's Division step short-circuits the boolean
            # check (True>=True) and divides the moment chromosome
            # replication completes — half the normal cell cycle.
            'divide': 'overwrite[divide_reset[boolean]]',
        }

    def ports_schema(self):
        return {
            "full_chromosome": {},
            "global_time": {"_default": 0.0},
            "divide": {
                "_default": False,
                "_updater": "set",
                "_divider": {"divider": "set_value", "config": {"value": False}},
            },
        }

    def next_update(self, timestep, states):
        division_time, has_triggered_division = attrs(
            states["full_chromosome"], ["division_time", "has_triggered_division"]
        )
        if len(division_time) < 2:
            return {}
        # All chromosomes already marked: no work to do this tick. The
        # composite-engine division Step (which fires on a separate
        # mass-threshold check) hasn't caught up yet — it will fire in
        # a later tick when mass crosses threshold. Returning {} avoids
        # an empty-array .min() crash from the next line.
        if has_triggered_division.all():
            return {}
        # Set division time to be the minimum division time for a chromosome
        # that has not yet triggered cell division
        divide_at_time = division_time[~has_triggered_division].min()
        if states["global_time"] >= divide_at_time:
            divide_at_time_index = np.where(division_time == divide_at_time)[0][0]
            has_triggered_division = has_triggered_division.copy()
            has_triggered_division[divide_at_time_index] = True
            # Set flag for ensuing division Step to trigger division
            return {
                "full_chromosome": {
                    "set": {"has_triggered_division": has_triggered_division}
                },
                "divide": True,
            }
        return {}


class Division(Step):
    """
    Division Deriver
     * Uses dry mass threshold that can be set in config via division_threshold
     * Samples division threshold from normal distribution centered around what
       is expected for a medium when division_threshold == mass_distribution
     * If flag d_period is set to true (default), mass thresholds are ignored and
       the same D period mechanism as wcEcoli is used.
    """

    name = NAME

    config_schema = {
        'agent_id': 'string',
        # composer is a class reference (e.g. Ecoli composer) that
        # Division instantiates on divide. Declared as Function so the
        # import path round-trips through bundle.
        'composer': 'function',
        # composer_config is the per-run sim config used to rebuild
        # daughter composites. A shallow map[string → node] keeps
        # the top-level string keys typed while leaving the
        # heterogeneous sub-values opaque.
        'composer_config': 'map[node]',
        # division_threshold can be:
        #   - boolean (config default placeholder)
        #   - string "mass_distribution" (dynamic-mass mode flag)
        #   - float (explicit mass threshold, set after first tick in
        #     mass_distribution mode)
        'division_threshold': 'union[boolean,string,float]',
        # dry_mass_inc_dict maps media_id → Unum[fg] mass increase.
        'dry_mass_inc_dict': 'map[unum[fg]]',
        'seed': 'lineage_seed[integer]',
        # daughter_ids_function generates daughter agent_ids at
        # division time.
        'daughter_ids_function': 'function',
    }

    defaults: Dict[str, Any] = {
        "daughter_ids_function": daughter_phylogeny_id,
        "threshold": None,
        "seed": 0,
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

        self.agent_id = self.parameters["agent_id"]
        self.composer = self.parameters["composer"]
        self.composer_config = self.parameters["composer_config"]
        self.random_state = np.random.RandomState(seed=self.parameters["seed"])

        self.division_mass_multiplier = 1
        if self.parameters["division_threshold"] == "mass_distribution":
            division_random_seed = (
                binascii.crc32(b"CellDivision", self.parameters["seed"]) & 0xFFFFFFFF
            )
            division_random_state = np.random.RandomState(seed=division_random_seed)
            self.division_mass_multiplier = division_random_state.normal(
                loc=1.0, scale=0.1
            )
        self.dry_mass_inc_dict = self.parameters["dry_mass_inc_dict"]

    def inputs(self):
        return {
            # division_variable is either the dry_mass (float, when
            # wired to 'dry_mass') or the divide flag (boolean, when
            # wired to 'divide' via MarkDPeriod's output).
            'division_variable': 'union[boolean,float]',
            'full_chromosome': 'unique_array',
            'media_id': 'string',
            'division_threshold': 'union[boolean,string,float]',
        }

    def outputs(self):
        return {
            'agents': {},  # _divide sentinel writes here; agents schema already exists
            # Same polymorphism as the input (see config_schema).
            'division_threshold': 'overwrite[union[boolean,string,float]]',
        }

    def ports_schema(self):
        return {
            "division_variable": {},
            "full_chromosome": {},
            "agents": {"*": {}},
            "media_id": {},
            "division_threshold": {
                "_default": self.parameters["division_threshold"],
                "_updater": "set",
                "_divider": {
                    "divider": "set_value",
                    "config": {"value": self.parameters["division_threshold"]},
                },
            },
        }

    def next_update(self, timestep, states):
        if states["division_threshold"] == "mass_distribution":
            current_media_id = states["media_id"]
            return {
                "division_threshold": (
                    states["division_variable"]
                    + self.dry_mass_inc_dict[current_media_id].asNumber(units.fg)
                    * self.division_mass_multiplier
                )
            }

        division_variable = states["division_variable"]

        if (division_variable >= states["division_threshold"]) and (
            states["full_chromosome"]["_entryState"].sum() >= 2
        ):
            daughter_ids = self.parameters["daughter_ids_function"](self.agent_id)
            daughter_updates = []
            for daughter_id in daughter_ids:
                config = dict(self.composer_config)
                config["agent_id"] = daughter_id
                config["seed"] = self.random_state.randint(0, RAND_MAX)
                # Regenerate composite to avoid unforeseen shared states
                composite = self.composer(config).generate()
                # Get shared process instances for partitioned processes
                process_states = {
                    process.parameters["process"].name: (process.parameters["process"],)
                    for process in composite.steps.values()
                    if "process" in process.parameters
                }
                initial_state = {
                    "process": process_states,
                }
                daughter_updates.append(
                    {
                        "key": daughter_id,
                        "processes": composite["processes"],
                        "steps": composite["steps"],
                        "flow": composite["flow"],
                        "topology": composite["topology"],
                        "initial_state": initial_state,
                    }
                )

            print(f"DIVIDE! MOTHER {self.agent_id} -> DAUGHTERS {daughter_ids}")

            return {
                "agents": {
                    "_divide": {"mother": self.agent_id, "daughters": daughter_updates}
                }
            }
        return {}


class CompositeDivision(Division):
    """v2-native subclass of Division.

    In process-bigraph, the ``_divide`` sentinel triggers a
    type-driven state split (``_handle_divide_sentinel`` →
    ``_divide_state``) that regenerates daughter stores and re-wires
    Link edges from the schema itself. The v1 Composer machinery
    (``self.composer(config).generate()``) is not needed — the
    framework pulls fresh instances directly from the schema.

    So this subclass:
      - drops ``composer`` and ``composer_config`` from its config
      - doesn't call ``generate()`` or build daughter composite dicts
      - emits a minimal ``_divide`` sentinel with just daughter keys,
        which is the format the v2 framework actually consumes
    """

    name = "ecoli-cell-division"

    config_schema = {
        'agent_id': 'string',
        'division_threshold': 'union[boolean,string,float]',
        'dry_mass_inc_dict': 'map[unum[fg]]',
        'seed': 'lineage_seed[integer]',
        'daughter_ids_function': 'function',
        # Single-lineage mode: emit only daughter 0 in the _divide
        # sentinel so the composite has exactly one agent at all
        # times. The agent_id grows by one '0' per division and the
        # cell line is followed in-place — no daughter proliferation,
        # no per-gen Python rebuild, no JSON shuttle.
        # Used by the composite_lineage engine.
        'single_daughters': 'boolean',
    }

    def outputs(self):
        # Override the parent's outputs() to seed division_threshold
        # with the configured value. Without this, v2's framework leaves
        # state.division_threshold = None at startup, and the boolean
        # comparison ``division_variable >= None`` (or ``False >= False``
        # if the framework substitutes a typed default) short-circuits
        # to True — division then fires the moment chromosome
        # replication completes, halving the cell cycle.
        threshold = self.parameters.get("division_threshold")
        return {
            'agents': {},
            'division_threshold': {
                '_type': 'overwrite[union[boolean,string,float]]',
                '_default': threshold,
            },
        }

    def inputs(self):
        # Same default seeding for the input side so the read sees the
        # configured threshold, not None, when no upstream write has
        # happened yet. The reset semantics live on the outputs side
        # (the divider), not here.
        threshold = self.parameters.get("division_threshold")
        return {
            'division_variable': 'union[boolean,float]',
            'full_chromosome': 'unique_array',
            'media_id': 'string',
            'division_threshold': {
                '_type': 'union[boolean,string,float]',
                '_default': threshold,
            },
        }

    def __init__(self, parameters=None):
        # Bypass Division.__init__ (which requires composer /
        # composer_config). Do Step.__init__ directly and populate
        # just the v2-relevant attributes.
        parameters = parameters or {}
        Step.__init__(self, parameters)
        self.agent_id = self.parameters["agent_id"]
        self.random_state = np.random.RandomState(seed=self.parameters["seed"])

        self.division_mass_multiplier = 1
        if self.parameters["division_threshold"] == "mass_distribution":
            division_random_seed = (
                binascii.crc32(b"CellDivision", self.parameters["seed"]) & 0xFFFFFFFF
            )
            division_random_state = np.random.RandomState(seed=division_random_seed)
            self.division_mass_multiplier = division_random_state.normal(
                loc=1.0, scale=0.1
            )
        self.dry_mass_inc_dict = self.parameters["dry_mass_inc_dict"]

    def update(self, states, interval=None):
        if states["division_threshold"] == "mass_distribution":
            current_media_id = states["media_id"]
            new_threshold = (
                states["division_variable"]
                + self.dry_mass_inc_dict[current_media_id].asNumber(units.fg)
                * self.division_mass_multiplier
            )
            # DEBUG: print the anchor when the threshold first
            # converts from "mass_distribution" string to a float.
            # Compare with v1's parquet cell_mass at the same tick.
            if os.environ.get('VECOLI_DEBUG_DIVISION'):
                import sys as _sys
                print(f"[div-debug] agent={self.agent_id} "
                      f"seed={self.parameters['seed']} "
                      f"anchor_mass={states['division_variable']:.10f} "
                      f"dry_mass_inc={self.dry_mass_inc_dict[current_media_id].asNumber(units.fg):.10f} "
                      f"multiplier={self.division_mass_multiplier:.10f} "
                      f"threshold={new_threshold:.10f}",
                      file=_sys.stderr, flush=True)
            return {"division_threshold": new_threshold}

        division_variable = states["division_variable"]
        if (division_variable >= states["division_threshold"]) and (
            states["full_chromosome"]["_entryState"].sum() >= 2
        ):
            daughter_ids = self.parameters["daughter_ids_function"](self.agent_id)
            # Single-lineage mode: drop daughter 1 so the composite
            # tracks one cell forward through divisions in-place.
            if self.parameters.get("single_daughters", False):
                daughter_ids = daughter_ids[:1]
            print(f"DIVIDE! MOTHER {self.agent_id} -> DAUGHTERS {daughter_ids}")
            # v2 framework only needs the daughter keys; state split
            # is schema-driven via _divide_state.
            return {
                "agents": {
                    "_divide": {
                        "mother": self.agent_id,
                        "daughters": [{"key": did} for did in daughter_ids],
                    }
                }
            }
        return {}


class DivisionDetected(Exception):
    pass


class StopAfterDivision(Process):
    """
    Detect division and raise an exception that must be caught.

    NOTE: This is a vivarium-only process. In the composite engine,
    division detection is handled by the driver loop checking agent count.
    """

    name = "stop-after-division"

    config_schema = {}

    def inputs(self):
        return {
            'agents': 'map[node]',
        }

    def outputs(self):
        return {}

    def ports_schema(self):
        return {
            "agents": {"*": {}},
        }

    def calculate_timestep(self, interval_or_state, state=None):
        if state is None:
            return 0
        return self.parameters.get('time_step', 1.0)

    def update_condition(self, timestep, states):
        if len(states["agents"]) > 1:
            raise DivisionDetected("More than one cell in agents store.")
        return False

    def next_update(self, timestep, states):
        raise RuntimeError("This should never be called.")
