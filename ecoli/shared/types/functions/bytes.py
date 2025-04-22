# -- bytes -- #

import pickle


def check_bytes(schema, state, core=None):
    return isinstance(state, bytes)


def apply_bytes(schema, current, update: bytes, core=None):
    return current + update


def deserialize_bytes(schema, encoded, core=None):
    return pickle.loads(encoded)