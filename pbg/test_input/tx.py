from vivarium.core.process import Process
from vivarium.library.units import units


class Tx(Process):

    defaults = {
        'ktsc': 1e-2,
        'kdeg': 1e-3
    }

    def __init__(self, parameters=None):
        super().__init__(parameters)

    def ports_schema(self):
        return {
            'DNA': {
                'G': {
                    '_default': 10 * units.mg / units.mL,
                    '_updater': 'accumulate',
                    '_emit': True}},
            'mRNA': {
                'C': {
                    '_default': 100 * units.mg / units.mL,
                    '_updater': 'accumulate',
                    '_emit': True}}}

    def next_update(self, timestep, states):
        G = states['DNA']['G']
        C = states['mRNA']['C']
        dC = (self.parameters['ktsc'] * G - self.parameters['kdeg'] * C) * timestep
        return {
            'mRNA': {
                'C': dC
            }
        }
