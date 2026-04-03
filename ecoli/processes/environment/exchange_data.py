from ecoli.processes.registries import topology_registry
from ecoli.library.ecoli_step import EcoliStep as Step
from vivarium.library.units import units

NAME = "exchange_data"
TOPOLOGY = {
    "boundary": ("boundary",),
    "environment": ("environment",),
}
topology_registry.register(NAME, TOPOLOGY)


class ExchangeData(Step):
    """
    Update metabolism exchange constraints according to environment concs.
    """

    name = NAME
    topology = TOPOLOGY

    config_schema = {
        'external_state': {'_type': 'node', '_default': None},
        'environment_molecules': 'list[string]',
        'saved_media': 'map[string]',
        'time_step': 'float{1.0}',
    }

    def inputs(self):
        return {
            'boundary': {'external': 'map[float]'},
            'environment': {
                'exchange_data': {
                    'constrained': 'map[float]',
                    'unconstrained': 'list[string]',
                },
            },
        }

    def outputs(self):
        return {
            'environment': {
                'exchange_data': {
                    'constrained': 'map[float]',
                    'unconstrained': 'list[string]',
                },
            },
        }

    def __init__(self, parameters=None):
        super().__init__(parameters)
        self.external_state = self.parameters["external_state"]
        self.environment_molecules = self.parameters["environment_molecules"]

    def ports_schema(self):
        return {
            "boundary": {"external": {"*": {"_default": 0 * units.mM}}},
            "environment": {
                "exchange_data": {
                    "constrained": {"_default": {}, "_updater": "set"},
                    "unconstrained": {"_default": set(), "_updater": "set"},
                }
            },
        }

    def update(self, states, interval=None):
        env_concs = {
            mol: states["boundary"]["external"][mol]
            for mol in self.environment_molecules
        }

        self.external_state.import_constraint_threshold *= units.mM
        exchange_data = self.external_state.exchange_data_from_concentrations(env_concs)
        self.external_state.import_constraint_threshold = (
            self.external_state.import_constraint_threshold.magnitude
        )

        unconstrained = exchange_data["importUnconstrainedExchangeMolecules"]
        constrained = exchange_data["importConstrainedExchangeMolecules"]
        return {
            "environment": {
                "exchange_data": {
                    "constrained": constrained,
                    "unconstrained": list(unconstrained),
                }
            }
        }
