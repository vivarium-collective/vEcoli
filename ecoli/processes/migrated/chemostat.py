from process_bigraph import Process


class Chemostat(Process):
    config_schema = {
        # Map from variable names to the values (must support
        # subtraction) those variables should be held at.
        "targets": "tree",
        "delay": "integer",
    }

    def __init__(self, config=None, core=None):
        super().__init__(config, core)
        self.seconds_to_wait = self.config["delay"]

    def inputs(self):
        schema = {
            variable: "float"
            for variable, target in self.config["targets"].items()
        }
        return schema

    def outputs(self):
        schema = {
            variable: "float"
            for variable, target in self.config["targets"].items()
        }
        return schema

    def update(self, state, interval):
        if self.seconds_to_wait > 0:
            self.seconds_to_wait -= interval
            return {}

        targets = self.config["targets"]
        update = {
            variable: targets[variable] - current
            for variable, current in state.items()
        }
        return update
