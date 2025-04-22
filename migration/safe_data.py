from ecoli.migrated.complexation import Complexation
from vivarium import Vivarium
from ecoli.migrated.registries import ecoli_core as ec
from ecoli.shared.utils.migration import Configuration, configure
from ecoli.shared.encryption import data, bitstring


def get_config():
    subpackage: str = "migrated"
    module_name: str = "complexation"
    process_class_name: str = "Complexation"
    initial_time: int = 0
    process_id = 'complexation'
    return configure(subpackage, module_name, process_class_name, initial_time)




