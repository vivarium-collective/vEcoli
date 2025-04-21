import numpy as np
import pytest


@pytest.fixture
def config():
    return {
        "stoichiometry": [[-1, 1, 0], [0, -1, 1], [1, 0, -1], [-1, 0, 1], [1, -1, 0], [0, 1, -1]],
        "rates": np.random.random((6,)).tolist(),
        "molecule_names": ["A", "B", "C"],
        "seed": 1,
        "reaction_ids": [1, 2, 3, 4, 5, 6],
        "complex_ids": [1, 2, 3, 4, 5, 6],
    }