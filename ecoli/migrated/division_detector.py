"""
---------------------------
MIGRATED: Division Detector
---------------------------
"""

from process_bigraph import Step


class DivisionDetector(Step):
    """Calculates division threshold for inner simulation in EngineProcess.
    Upon reaching threshold, sets a flag through the `division_trigger` port
    that can be detected via a tunnel and used to initiate division.

    By default, we forgo the dry mass threshold in favor of a boolean
    threshold set by the MarkDPeriod Step in ecoli.processes.cell_division.
    Users can revert to the mass threshold by setting d_period to False in
    their config json.
    """
    config_schema = {
        "division_threshold": {
            "_type": "string",
            "_default": "massDistribution"
        },
        "division_variable": None,
        "chromosome_path": None,
        "dry_mass_inc_dict": None,
        "division_mass_multiplier": {
            "_type": "integer",
            "_default": 1,
        },
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.division_threshold = self.config["division_threshold"]
        self.dry_mass_inc_dict = self.config["dry_mass_inc_dict"]
        self.division_mass_multiplier = self.config["division_mass_multiplier"]

    def inputs(self):
        return {
            "division_threshold": {
                "_default": self.division_threshold,
                "_divide": "set_value",
                "_type": "string"
            },
            "division_variable": "tree",
            "full_chromosomes": "tree",  # TODO: this should really be a numpy dtype (list[tuple[id, _entryState]])
            "media_id": "tree",
            "division_trigger": "boolean"
        }

    def outputs(self):
        return {
            "division_trigger": {
                "_default": False,
                "_type": "boolean",
                "_divide": "set_value",
            },
            "division_threshold": {
                "_default": self.division_threshold,
                "_divide": "set_value",
                "_type": "string"
            }
        }

    def update(self, state):
        update = {}
        division_threshold = state["division_threshold"]
        if division_threshold == "massDistribution":
            mass_inc = self.dry_mass_inc_dict[state["media_id"]]
            division_threshold = (
                state["division_variable"]
                + mass_inc.asNumber() * self.division_mass_multiplier
            )
            update["division_threshold"] = division_threshold
        if (state["division_variable"] >= division_threshold) and (
            state["full_chromosomes"]["_entryState"].sum() >= 2
        ):
            update["division_trigger"] = True
        return update
