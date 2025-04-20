from typing import Any

import numpy as np
from numpy.typing import NDArray


bulk_dtype: np.dtype = np.dtype([
    ("id", "<U100"),
    ("count", "<f8")
])

active_replisomes_dtype: np.dtype = np.dtype([
    ("id", "<U100"),
    ("_entryState", np.ndarray | Any)
])


# def format_state(state: dict, key: str, dtype: np.dtype) -> NDArray[bulk_dtype]:
#     """"""
#     return np.array(state[key], dtype=dtype)
# 
# 
# def format_bulk_state(state: dict) -> NDArray[bulk_dtype]:
#     """"""
#     return format_state(state, "bulk", bulk_dtype)
# 
# 
# def format_active_replisomes_state(state: dict) -> NDArray[active_replisomes_dtype]:
#     return format_state(state, "active_replisomes", active_replisomes_dtype)
