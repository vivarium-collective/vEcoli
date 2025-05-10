from ecoli.migrated.complexation import Complexation
from vivarium import Vivarium
from ecoli.shared.registry import ecoli_core as ec
from ecoli.shared.utils.migration import configure


def get_config():
    subpackage: str = "migrated"
    module_name: str = "complexation"
    process_class_name: str = "Complexation"
    initial_time: int = 0
    process_id = 'complexation'
    return configure(subpackage, module_name, process_class_name, initial_time)




