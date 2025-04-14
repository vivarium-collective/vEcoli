import abc

from process_bigraph import Step, Process

from ecoli.shared.schemas import get_config_schema


class StepBase(Step):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state):
        pass


class ProcessBase(Process):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        super().__init__(config, core)
        self.timestep = self.config["time_step"]

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state, interval):
        pass
