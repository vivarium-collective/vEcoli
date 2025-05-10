import numpy as np


bulk_dtype: np.dtype = np.dtype([
    ("id", "<U100"),
    ("count", "<f8"),
    ("_entryState", np.int8)
])
