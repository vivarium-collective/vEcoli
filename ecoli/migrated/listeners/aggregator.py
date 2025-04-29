"""
==========
MIGRATED: Aggregator
==========

Given a list of paths and a list of functions, this will apply the ith
function to the ith path and write the results through `aggregated`.
If a list of functions is not supplied, len is used for all paths.
"""


import copy
from vivarium.core.process import Step
from vivarium.library.topology import assoc_path, get_in

from ecoli.shared.interface import StepBase
from ecoli.shared.utils.schemas import collapse_defaults, get_defaults_schema


class Aggregator(StepBase):
    """
    Given a list of paths and a list of functions, this will apply the ith
    function to the ith path and write the results through `aggregated`.
    If a list of functions is not supplied, len is used for all paths.
    """
    name = "aggregator"
    defaults = {"paths": tuple(), "funcs": tuple()}

    def initialize(self, config):
        self.paths = config["paths"]
        if not config["funcs"]:
            self.funcs = (len,) * len(self.paths)
        else:
            self.funcs = config["funcs"]

        schema = {}
        variables = []
        for path, func in zip(self.paths, self.funcs):
            assoc_path(schema, path, {"_default": {}})
            variable = f"{path[-1]}_{func.__name__}"
            assert variable not in variables
            variables.append(variable)
        
        assert "aggregated" not in schema

        self.input_ports = schema
        self.output_ports = {
            "aggregated": {
                variable: "integer"
                for variable in variables
            }
        }

    def inputs(self):
        return self.input_ports

    def outputs(self):
        return self.output_ports

    def update(self, state, interval):
        counts = {}
        for path, func in zip(self.paths, self.funcs):
            variable = f"{path[-1]}_{func.__name__}"
            assert variable not in counts
            counts[variable] = func(get_in(state, path))
            assert counts[variable] is not None

        return {"aggregated": counts}


def len_squared(x):
    return len(x) ** 2


def len_plus_one(x):
    return len(x) + 1


def test_aggregator():
    # TODO: fix this
    
    from ecoli.shared.registry import ecoli_core as ec
    state = {
        "a": {
            "b": {
                '1': 0,
                '2': 0,
                '3': 0,
            },
            "c": {},
        },
    }
    proc = Aggregator(
        {
            "paths": (("a", "b"), ("a", "c"), ("a", "b")),
            "funcs": (len_squared, len_plus_one, len_plus_one),
        },
        core=ec
    )
    schema = proc.get_schema()
    expected_schema = {
        "a": {
            "b": {},
            "c": {}
        },
        "aggregated": {
            "b_len_squared": "integer",
            "c_len_plus_one": "integer",
            "b_len_plus_one": "integer"
        },
    }
    assert schema == expected_schema
    update = proc.update(0, state)
    expected_update = {
        "aggregated": {
            "b_len_squared": 9,
            "c_len_plus_one": 1,
            "b_len_plus_one": 4,
        }
    }
    assert update == expected_update


if __name__ == "__main__":
    test_aggregator()
