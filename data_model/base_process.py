import process_bigraph as pbg

from vivarium.core.process import Process as VivariumProcess
from vivarium.core.types import State


class BaseProcess(VivariumProcess, pbg.Process):
    config_schema = {}

    def __init__(self, config=None, core=None):
        super().__init__(parameters=config)
        pbg.Process.__init__(self, config=config, core=core)

    # --- methods inherited from vivarium.core --- #
    def ports_schema(self):
        return super().ports_schema()

    def next_update(self, timestep, states: State):
        return super().next_update(timestep, states)

    # --- methods which extend pbg.Edge() --- #
    def inputs(self):
        return super().inputs()

    def outputs(self):
        return super().outputs()

    def initial_state(self, config=None):
        return super().initial_state(config)

    def update(self, state, interval):
        return super().update(state, interval)
