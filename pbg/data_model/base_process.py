import abc

import process_bigraph as pbg

from vivarium.core.process import Process as VivariumProcess
from vivarium.core.types import State


CORE = pbg.ProcessTypes()


class MetaABCAndType(abc.ABCMeta, type):
    pass


class BaseProcess(pbg.Process, VivariumProcess, metaclass=MetaABCAndType):
    config_schema = {}

    def __init__(self, config=None, core=CORE):
        super().__init__(config=config, core=core)
        VivariumProcess.__init__(self, parameters=config)

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
