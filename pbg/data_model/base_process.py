import abc

from process_bigraph import ProcessTypes, Process as PbgProcess
from vivarium.core.process import Process as VivariumProcess

from pbg import parse
from pbg.translate import get_port_mapping, get_config_schema

CORE = ProcessTypes()


class MetaABCAndType(abc.ABCMeta, type):
    pass


class BaseProcess(PbgProcess, VivariumProcess, metaclass=MetaABCAndType):
    """This should replace all instances of inheritance from `vivarium.core.process.Process` as the new base class type."""
    config_schema = {}

    def __init__(self, parameters=None, core=None):
        VivariumProcess.__init__(self, parameters=parameters)

        self.config_schema = get_config_schema(self.parameters)
        super().__init__(config=parameters, core=core)

    # --- methods which extend pbg.Edge() --- #
    def inputs(self):
        # TODO: currently only extends ports bidirectionally. Change this
        return self._ports()

    def outputs(self):
        return self._ports()

    def initial_state(self):
        return parse.find_defaults(self.ports_schema())

    def update(self, state, interval):
        return self.next_update(timestep=interval, states=state)

    def _ports(self):
        ports_schema = self.ports_schema()
        return get_port_mapping(ports_schema)
