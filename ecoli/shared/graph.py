from dask.distributed import Client
import numpy as np
import rustworkx as rx
from rustworkx.visualization import mpl_draw as draw_graph


def construct_state_graph(state: dict) -> rx.PyGraph:
    nodes = list(state.keys())
    graph = rx.PyGraph()
    graph.add_nodes_from(nodes)
    edge_list = [
        (*pair, 1.0)
        for pair in get_connectivity([i for i, v in enumerate(nodes)])
    ]
    graph.add_edges_from(edge_list)
    return graph


def get_connectivity(data, group_size: int = 2) -> list[tuple]:
    import itertools
    return list(itertools.combinations(data, group_size))


test_data = {'a': 2, 'b': 3, 'c': 11}

def test_dask():
    client = Client()
    x = client.submit(np.arange, 10)  # [0, 1, 2, 3, ...]

    def f(arr):
        arr[arr > 5] = 0  # modifies input directly without making a copy
        arr += 1          # modifies input directly without making a copy
        return arr

    y = client.submit(f, x)