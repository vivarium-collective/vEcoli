from dask.distributed import Client
import numpy as np


client = Client()
x = client.submit(np.arange, 10)  # [0, 1, 2, 3, ...]

def f(arr):
    arr[arr > 5] = 0  # modifies input directly without making a copy
    arr += 1          # modifies input directly without making a copy
    return arr

y = client.submit(f, x)