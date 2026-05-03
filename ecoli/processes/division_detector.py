from ecoli.library.bigraph_bridge import BigraphStep as Step


class DivisionDetector(Step):
    """Calculates division threshold for inner simulation in EngineProcess.
    Upon reaching threshold, sets a flag through the `division_trigger` port
    that can be detected via a tunnel and used to initiate division.

    By default, we forgo the dry mass threshold in favor of a boolean
    threshold set by the MarkDPeriod Step in ecoli.processes.cell_division.
    Users can revert to the mass threshold by setting d_period to False in
    their config json.
    """

    name = "division-detector"

    config_schema = {
        # See Division.config_schema in cell_division.py for the
        # rationale behind these union / map types — same
        # polymorphism applies here.
        'division_threshold': 'union[boolean,string,float]',
        # division_variable is either a boolean (divide flag from
        # MarkDPeriod) or a float (dry mass).
        'division_variable': 'union[boolean,float]',
        # Wire path tuple passed through config so downstream knows
        # where to read the full_chromosomes store.
        'chromosome_path': 'list[string]',
        'dry_mass_inc_dict': 'map[unum[fg]]',
        'division_mass_multiplier': 'float{1.0}',
    }

    defaults = {
        "division_threshold": None,
        "division_variable": None,
        "chromosome_path": None,
        "dry_mass_inc_dict": None,
        "division_mass_multiplier": 1,
    }

    def __init__(self, config):
        super().__init__(config)
        self.division_threshold = self.parameters["division_threshold"]
        self.dry_mass_inc_dict = self.parameters["dry_mass_inc_dict"]
        self.division_mass_multiplier = self.parameters["division_mass_multiplier"]

    def inputs(self):
        return {
            'division_variable': 'union[boolean,float]',
            'full_chromosomes': 'unique_array',
            'media_id': 'string',
            'division_threshold': 'union[boolean,string,float]',
        }

    def outputs(self):
        return {
            # divide_reset matches v1's per-port ``_divider: set_value``
            # so the daughter starts with division_trigger=False and
            # division_threshold reset to its configured default rather
            # than carrying over the mother's mid-cell-cycle values.
            'division_trigger': 'overwrite[boolean]',
            'division_threshold': 'overwrite[union[boolean,string,float]]',
        }

    def ports_schema(self):
        return {
            "division_variable": {},
            "division_trigger": {
                "_default": False,
                "_updater": "set",
                "_divider": {"divider": "set_value", "config": {"value": False}},
            },
            "full_chromosomes": {},
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
        update = {}
        division_threshold = states["division_threshold"]
        if division_threshold == "mass_distribution":
            mass_inc = self.dry_mass_inc_dict[states["media_id"]]
            division_threshold = (
                states["division_variable"]
                + mass_inc.asNumber() * self.division_mass_multiplier
            )
            update["division_threshold"] = division_threshold
        if (states["division_variable"] >= division_threshold) and (
            states["full_chromosomes"]["_entryState"].sum() >= 2
        ):
            update["division_trigger"] = True
        return update
