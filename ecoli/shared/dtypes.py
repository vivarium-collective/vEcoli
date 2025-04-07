import numpy as np
from numpy.typing import NDArray


BULK_DTYPE: np.dtype = np.dtype([
    ("id", "<U100"),
    ("count", "<f8")
])


def format_bulk_state(state: dict) -> NDArray[BULK_DTYPE]:
    """"""
    return np.array(state["bulk"], dtype=BULK_DTYPE)
