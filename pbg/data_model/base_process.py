import abc

from process_bigraph import ProcessTypes, Process as PbgProcess
from vivarium.core.process import Process as VivariumProcess

from pbg.parse import get_port_mapping, get_config_schema


CORE = ProcessTypes()


class MetaABCAndType(abc.ABCMeta, type):
    pass


class BaseProcess(PbgProcess, VivariumProcess, metaclass=MetaABCAndType):
    """This should replace all instances of inheritance from `vivarium.core.process.Process` as the new base class type."""
    # config_schema = {}
    # defaults = {}
    # name = "base_process"

    def __init__(self, parameters=None):
        VivariumProcess.__init__(self, parameters=parameters)

        self.config_schema = get_config_schema(self.parameters)
        super().__init__(config=parameters, core=CORE)

    # --- methods inherited from vivarium.core --- #
    def ports_schema(self):
        return super().ports_schema()

    def next_update(self, timestep, states):
        return super().next_update(timestep, states)

    # --- methods which extend pbg.Edge() --- #
    def inputs(self):
        # TODO: currently only extends ports bidirectionally. Change this
        return self._ports()

    def outputs(self):
        return self._ports()

    def update(self, state, interval):
        return self.next_update(interval, state)

    def _ports(self):
        ports_schema = self.ports_schema()
        return get_port_mapping(ports_schema)
