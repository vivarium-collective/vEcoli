import abc

from process_bigraph import Step, Process

from ecoli.shared.schemas import get_config_schema


class Base(abc.ABC):
    defaults = {}
    config_schema = {}

    def __init__(self, config=None, core=None):
        self.timestep_schema = {"_default": 1.0, "_type": "float"}
        self.config_schema = get_config_schema(self.defaults)
        self.config_schema['time_step'] = self.timestep_schema
        self.timestep = config.get("time_step", 1.0)

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state):
        pass


class StepBase(Base, Step):
    def __init__(self, config=None, core=None):
        super().__init__(config, core)

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state):
        pass


class ProcessBase(Base, Process):
    def __init__(self, config=None, core=None):
        super().__init__(config, core)

    @abc.abstractmethod
    def inputs(self):
        pass

    @abc.abstractmethod
    def outputs(self):
        pass

    @abc.abstractmethod
    def update(self, state):
        pass


# class StepBase(Step):
#     defaults = {}
#     config_schema = {}
#
#     def __init__(self, config=None, core=None):
#         self.timestep_schema = {"_default": 1.0, "_type": "float"}
#         self.config_schema = get_config_schema(self.defaults)
#         self.config_schema['time_step'] = self.timestep_schema
#
#         super().__init__(config, core)
#         self.timestep = self.config["time_step"]
#
#     @abc.abstractmethod
#     def inputs(self):
#         pass
#
#     @abc.abstractmethod
#     def outputs(self):
#         pass
#
#     @abc.abstractmethod
#     def update(self, state):
#         pass
#
#
# class ProcessBase(Process):
#     defaults = {}
#     config_schema = {}
#
#     def __init__(self, config=None, core=None):
#         self.timestep_schema = {"_default": 1.0, "_type": "float"}
#         self.config_schema = get_config_schema(self.defaults)
#         self.config_schema['time_step'] = self.timestep_schema
#
#         super().__init__(config, core)
#         self.timestep = self.config["time_step"]
#
#     @abc.abstractmethod
#     def inputs(self):
#         pass
#
#     @abc.abstractmethod
#     def outputs(self):
#         pass
#
#     @abc.abstractmethod
#     def update(self, state, interval):
#         pass
