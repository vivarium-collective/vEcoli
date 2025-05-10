"""
# required bgs keys: {'_default', '_apply', '_check', '_serialize', '_deserialize', '_fold'}
# optional bgs keys: {'_type', '_value', '_description', '_type_parameters', '_inherit', '_divide'}
"""

__all__ = [
    "type_name", "apply", "check", "divide", "serialize", "deserialize", "default"
]


def type_name():
    raise NotImplementedError("Please use the specific type module.")


def apply(schema, current, update, top_schema, top_state, path, core):
    raise NotImplementedError("Please use the specific type module.")


def check(schema, state, core):
    raise NotImplementedError("Please use the specific type module.")  


def divide(schema, state, values, core):
    raise NotImplementedError("Please use the specific type module.")


def serialize(schema, value, core):
    raise NotImplementedError("Please use the specific type module.")


def deserialize(schema, encoded: str, core):
    raise NotImplementedError("Please use the specific type module.")


def default():
    raise NotImplementedError("Please use the specific type module.")