# -- quantity -- #

import pickle
from pint import Quantity


def deserialize_quantity(value, core=None):
    return str(Quantity(value))


def serialize_quantity(value, core=None):
    return pickle.dumps(value.m)


def apply_quantity(schema, current: Quantity, update: Quantity, core=None):
    return current + update