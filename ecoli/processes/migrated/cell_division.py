"""
=============
MIGRATED: Cell Division
=============
"""
from types import FunctionType
from typing import Any, Dict, Callable

import binascii
import numpy as np
# from vivarium.core.process import Step
from process_bigraph import Step

from ecoli.library.sim_data import RAND_MAX
from ecoli.library.schema import attrs
from wholecell.utils import units

NAME = "ecoli-cell-division"


def daughter_phylogeny_id(mother_id):
    return [str(mother_id) + "0", str(mother_id) + "1"]


class MarkDPeriod(Step):
    """Set division flag after D period has elapsed"""
    name = "mark_d_period"

    def ports_schema(self):
        return {
            "full_chromosome": {},
            "global_time": "float",
            "divide": {
                "_default": False,
                "_updater": "set",
                "_divider": {"divider": "set_value", "config": {"value": False}},
            },
        }
    
    def inputs(self):
        return {
            "global_time": "float",
            "full_chromosome": "metadata_array_type"
        }
    
    def outputs(self): 
        return {
            "full_chromosome": "tree",
            "divide": "boolean"
        }

    def initial_state(self):
        return {
            "full_chromosome": {},
            "divide": False
        }

    def update(self, state):
        division_time, has_triggered_division = attrs(
            states=state["full_chromosome"],
            attributes=["division_time", "has_triggered_division"]
        )

        # TODO: what to do here?
        if len(division_time) < 2:
            return {}
        # Set division time to be the minimum division time for a chromosome
        # that has not yet triggered cell division
        divide_at_time = division_time[~has_triggered_division].min()
        if state["global_time"] >= divide_at_time:
            divide_at_time_index = np.where(division_time == divide_at_time)[0][0]
            has_triggered_division = has_triggered_division.copy()
            has_triggered_division[divide_at_time_index] = True
            # Set flag for ensuing division Step to trigger division
            return {
                "full_chromosome": {
                    "set": {
                        "has_triggered_division": has_triggered_division
                    }
                },
                "divide": True,
            }
        return {}


class Division(Step):
    """
    Division Deriver
     * Uses dry mass threshold that can be set in config via division_threshold
     * Samples division threshold from normal distribution centered around what
       is expected for a medium when division_threshold == massDistribution
     * If flag d_period is set to true (default), mass thresholds are ignored and
       the same D period mechanism as wcEcoli is used.
    """

    name = NAME
    config_schema = {
        "agent_id": "string",
        "seed": "integer",
        "division_threshold": "maybe[string]",  # <-- Union[str, None] = None by default
        "dry_mass_inc_dict": "tree"
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)

        self.daughter_ids_function: Callable[[str], list[str]] = daughter_phylogeny_id

        self.seed = self.config.get("seed", 0)
        # must provide a composer to generate new daughters
        self.agent_id = self.config["agent_id"]
        # self.composer = self.parameters["composer"]
        # self.composer_config = self.parameters["composer_config"]
        self.random_state = np.random.RandomState(seed=self.seed)

        self.division_mass_multiplier = 1
        if self.config["division_threshold"] == "massDistribution":
            division_random_seed = (
                binascii.crc32(b"CellDivision", self.seed) & 0xFFFFFFFF
            )
            division_random_state = np.random.RandomState(seed=division_random_seed)
            self.division_mass_multiplier = division_random_state.normal(
                loc=1.0, scale=0.1
            )
        self.dry_mass_inc_dict = self.config["dry_mass_inc_dict"]

    def inputs(self):
        return {
            "agents": "tree"
        }

    def outputs(self):
        return {
            "division_variable": {},
            "full_chromosome": {},
            "agents": {"*": {}},  # TODO: what to do here?
            "media_id": {},
            "division_threshold": {
                "_default": self.config["division_threshold"],
                # "_updater": "set",
                # "_divider": {
                #     "divider": "set_value",
                #     "config": {"value": self.parameters["division_threshold"]},
                # },
            },
        }

    def update(self, state):
        # Figure out division threshold at first timestep if
        # using massDistribution setting
        if state["division_threshold"] == "massDistribution":
            current_media_id = state["media_id"]
            return {
                "division_threshold": (
                    state["division_variable"]
                    + self.dry_mass_inc_dict[current_media_id].asNumber(units.fg)
                    * self.division_mass_multiplier
                )
            }

        division_variable = state["division_variable"]

        if (division_variable >= state["division_threshold"]) and (
            state["full_chromosome"]["_entryState"].sum() >= 2
        ):
            daughter_ids = self.daughter_ids_function(self.agent_id)
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
                initial_state = {"process": process_states}
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


class DivisionDetected(Exception):
    pass


class StopAfterDivision(Step):
    """
    Detect division and raise an exception that must be caught.
    """

    name = "stop-after-division"

    def ports_schema(self):
        return {
            "agents": {"*": {}},
        }

    def next_update(self, timestep, states):
        # Raise exception once division has occurred
        if len(states["agents"]) > 1:
            raise DivisionDetected("More than one cell in agents store.")
        return {}
