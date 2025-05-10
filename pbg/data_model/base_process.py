import abc

from process_bigraph import ProcessTypes, Process as PbgProcess
from vivarium.core.process import Process as VivariumProcess

from pbg import parse
from pbg.data_model.meta import MetaABCAndType
from pbg.translate import get_port_mapping, get_config_schema


class BaseProcess(PbgProcess, VivariumProcess, metaclass=MetaABCAndType):
    """
    This should replace all instances of inheritance from `vivarium.core.process.Process` as the new base class type.
    TODO: Ports are currently mapped 1:1 with same inputs and outputs. Change this! (somehow derive vivarium1.0 port directions)
    """
    config_schema = {}

    def __init__(self, parameters=None, core=None):
        VivariumProcess.__init__(self, parameters=parameters)

        self.config_schema = get_config_schema(self.parameters)
        super().__init__(config=parameters, core=core)

        self._register_ports()

    # --- methods which extend pbg.Edge() --- #
    def inputs(self):
        return self._ports

    def outputs(self):
        return self._ports

    def initial_state(self):
        return parse.find_defaults(self.ports_schema())

    def update(self, state, interval):
        update_data = state.copy()
        next_update = self.next_update(timestep=interval, states=state)
        update_data.update(next_update)
        return update_data

    @property
    def stores(self):
        # return self.ports()
        state = self.initial_state()
        return parse.find_stores(state)

    @property
    def _ports(self):
        ports_schema = self.ports_schema()
        return get_port_mapping(ports_schema)

    def _register_ports(self):
        for k, v in self._ports.items():
            port_type = v
            type_name = k + '_type'
            self.core.register(type_name, port_type)



