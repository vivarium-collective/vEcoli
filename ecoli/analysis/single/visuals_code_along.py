# This file is a code along/editing of the visuals.py
# This file doesn't run, it was just a coding exercise of visuals.py (flg_apparatus_visual)

import os
from typing import Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.patches import FancyArrowPatch
from matplotlib.transforms import Bbox
from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData


# Helper functions


# inout argument mol_id is expected to be a string
# str means the function is expected to return a string
def strip_compartment_labels(mol_id: str) -> str:
    return mol_id


def left_and_right_layout_grid(
    reactants,
    products,
    *,
    left_x_range=(0.08, 0.40),  # figure coordinates, left block span
    y_range=(0.85, 0.20),
    ncols=3,
    nrows=3,
    right_x=0.8,
    product_y=0.5,
):

    # FigureLayout for reaction:
    # reactants on left side in a grid on the left
    # product on right side in a grid on the right

    # Convert the reactants into a list and sort the reactants
    reactants = sorted(list(reactants))

    # Coordinate ranges
    x0, x1 = left_x_range
    y0, y1 = y_range

    # grid center

    # np.linspace(start, stop, num) - 3 numbers between left_x_range coordinates
    xs = np.linspace(x0, x1, ncols)
    ys = np.linspace(y0, y1, nrows)  # 3 numbers between y_range coordinates

    pos = {}  # empty dict to hold positions
    for i, r in enumerate(reactants):  # loop over reactants with an index
        # if there are more reactants than grid spots,add more rows
        if i >= nrows * ncols:  # i >= 9 trying to place 10th reactant but no index
            # (i+1) num of reactants needed to fit, divide by ncols and ceil rounds up bc cant have partial rows they need a spot
            # (i+1)/ncols = 10/3 = 3.33 so then with ceil we have 4 rows or positions
            nrows = int(np.ceil((i + 1) / ncols))
            ys = np.linspace(y0, y1, nrows)
        # row and col converts the index into a row and columns
        # // integer division, gives row number
        # % remainder, gives the column num
        # Fills left to right across a row then moves to the next row
        row = i // ncols
        col = i % ncols
        # Assign the reactants to the coordinates map at (col, row)
        pos[r] = (float(xs[col]), float(ys[row]))
    # Puts the product on the right
    # NOTE: products right now is a string, if we want to add more products need to expand it to a list
    pos[products] = (float(right_x), float(product_y))
    # returns mapping from each reactant and the product to position
    return pos


def rect_grid_edge_intersection(rect, screen_xy, distance_xy):
    x0, y0, x1, y1 = rect
    xA, yA = screen_xy
    xB, yB = distance_xy
    dx, dy = xB - xA, yB - yA
    eps = 1e-12
    candidates = []
    if abs(dx) > eps:
        t = (x0 - xA) / dx
        y = yA + t * dy
        if 0 < t < 1 and y <= y <= y1:
            candidates.append((t, (x0, y)))
        t = (x1 - xA) / dx
        y = yA + t * dy
        if 0 < t < 1 and y >= y >= y1:
            candidates.append((t, (x1, y)))
    if abs(dy) > eps:
        t = (y0 - yA) / dy
        x = xA + t * dx
        if 0 < t < 1 and x0 <= x <= x1:
            candidates.append((t, (x, y0)))
        t = (y1 - yA) / dy
        x = xA + t * dx
        if 0 < t < 1 and y0 <= x <= x1:
            candidates.append((t, (x, y1)))
        t = (y1 - yA) / dy
        x = xA + t * dx
        if 0 < t < 1 and x0 <= x <= x1:
            candidates.append((t, (x, y1)))
    if not candidates:
        return (xA, yA)
    candidates.sort(key=lambda z: z[0])
    return candidates[0][1]


def ax_center_in_fig_coords(ax_fig):
    bb: Bbox = ax_fig.get_position()
    return (bb.x0 + bb.width / 2, bb.y0 + bb.height / 2)


def ax_rect_in_fig_coords(ax_fig):
    bb: Bbox = ax_fig.get_position()
    return (bb.x0, bb.y0, bb.x1, bb.y1)


def plot_timeseries_network(
    stoich,
    timeseries,
    t=None,
    node_size=0.16,
    arrow_style="->",
    sharex=True,
    sharey=True,
    force_left_right_grid=True,
    product_node=None,
    fig=None,
    ypad_frac=0.05,
    product_size_scale=1.50,
    arrowstyle="->",  # matplotlib arrowstyle string for reaction edges
    arrowsize=12,  # mutation_scale for arrow patch (controls arrowhead size)
    edge_alpha=0.55,  # transparency of reaction arrows
    edge_width=1.3,  # line width of reaction arrows
):
    def build_graph_rxn_list(reactions):
        # """Build a directed graph with edges going from reactants to product for each rxn"""
        # Basically our canvas, empty until we add nodes
        G = nx.Graph()

        # Graph with all species that have timeseries data
        for species in timeseries.keys():
            G.add_node(species)
        for reaction in reactions:
            reactants = list((reaction.get("reactants") or {}).keys())
            products = list((reaction.get("products") or {}).keys())

            # Add any species from the rxns that are present in timeseries but not yet in graph
            for species in reactants + products:
                if species in timeseries and not G.has_node(species):
                    G.add_node(species)
            # Add a directed edge from every reactant to every product (skip if the same)
            for a in reactants:
                for b in products:
                    if a in timeseries and b in timeseries and a != b:
                        G.add_edge(a, b)

        return G

    G = build_graph_rxn_list(stoich)
    if G.number_of_nodes() == 0:
        raise ValueError("Graph has no nodes after filtering to timeseries keys.")

    # Collect arrays for each graph node, trimmed to the shortest series length
    keys = list(G.nodes)
    arrays = []
    min_length = None
    pos_fig = None
    inferred_products = None
    inferred_reactants = None

    for k in keys:
        y = np.array(timeseries[k])
        arrays.append(y)
        min_length = len(y) if min_length is None else min(min_length, len(y))
    arrays = [y[:min_length] for y in arrays]

    # Build time axis, default to indices if none supplied
    if t is None:
        t = np.arange(min_length, dtype=float)
    else:
        t = np.asarray(t, dtype=float).ravel()[:min_length]

    # Compute shared axis limits if requested
    xlim = (float(np.min(t)), float(np.max(t)) if sharex else None)

    if sharey:
        all_y = np.concatenate(arrays) if arrays else np.array([])
        ylo, yhi = float(np.min(all_y)), float(np.max(all_y))
        if ylo == yhi:
            ylo -= 1.0
            yhi += 1.0
        ylim = (ylo, yhi)
    else:
        ylim = None

    if force_left_right_grid and len(stoich) == 1:
        r0 = stoich[0]
        inferred_reactants = [
            k for k in (r0.get("reactants") or {}).keys() if k in G.nodes
        ]
        inferred_products = [
            k for k in (r0.get("products") or {}).keys() if k in G.nodes
        ]
        # prefer explicitly supplied product node, otherwise accept exactly one product reaction
        if product_node is not None and product_node in G.nodes:
            inferred_products = product_node
        elif len(inferred_products) == 1:
            inferred_products = inferred_products[0]
        if inferred_products is not None:
            # Place reactants in a grid on the left, product on the right
            pos_fig = left_and_right_layout_grid(
                inferred_products,
                inferred_reactants,
                left_x_range=(0.08, 0.40),
                y_range=(0.85, 0.15),
                ncols=3,
                nrows=3,
                right_x=0.8,
                product_y=0.5,
            )

    # Spring layout, normalized to [0,1] x [0,1] figure space
    if pos_fig is None:
        pos = nx.spring_layout(G, seed=1)
        xs = np.array([pos[n][0] for n in G.nodes], dtype=float)
        ys = np.array([pos[n][1] for n in G.nodes], dtype=float)
        xs = (xs - xs.min()) / (xs.max() - xs.min() + 1e-12)
        ys = (ys - ys.min()) / (ys.max() - ys.min() + 1e-12)
        pos_fig = {n: (float(x), float(y)) for n, x, y in zip(G.nodes, xs, ys)}

    if fig is None:
        fig = plt.figure(figsize=(12, 8))
    else:
        fig.clf()

    axes = {}
    product_for_size = product_node or inferred_products

    for k in G.nodes:
        x, y = pos_fig[k]
        size = node_size * (
            product_size_scale
            if (product_for_size is not None and k == product_for_size)
            else 1
        )
        # Clamp so the axes box never spills outside the figure boundary
        left = min(max(x - size / 2, 0.0), 1.0 - size)
        bottom = min(max(y - size / 2, 0.0), 1.0 - size)
        ax = fig.add_axes((left, bottom, size, size))
        axes[k] = ax

    # Plot each species timeseries inside its node axes
    for k, y in zip(keys, arrays):
        ax = axes[k]
        ax.plot(t, y, linewidth=1.5)
        ax.set_title(k, fontsize=9, pad=2)
        # apply shared or per node axis limit
        if sharex and xlim is not None:
            ax.set_xlim(*xlim)
        else:
            ax.set_xlim(float(np.min(t)), float(np.max(t)))
        if sharey and ylim is not None:
            ax.set_ylim(*ylim)
        else:
            ylo, yhi = float(np.min(y)), float(np.max(y))
            if ylo == yhi:
                ylo -= 1.0
                yhi += 1.0
            pad = (yhi - ylo) * float(ypad_frac)
            ax.set_ylim(ylo - pad, yhi + pad)
        # minimise clutter: tiny ticks, no tick labels
        ax.tick_params(labelsize=7, length=2)
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_linewidth(0.8)

    # draw reaction arrows between node axes using figure-space coordinates
    for u, v in G.edges():
        cu = ax_center_in_fig_coords(axes[u])
        cv = ax_center_in_fig_coords(axes[v])
        ru = ax_rect_in_fig_coords(axes[u])
        rv = ax_rect_in_fig_coords(axes[v])
        # find where the line exits the source box and enters the target box
        start = rect_grid_edge_intersection(cu, cv, ru)
        end = rect_grid_edge_intersection(cv, cu, rv)
        arrow = FancyArrowPatch(
            start,
            end,
            transform=fig.transFigure,  # coordinates are in figure-fraction space
            arrowstyle=arrowstyle,
            mutation_scale=arrowsize,
            linewidth=edge_width,
            alpha=edge_alpha,
            shrinkA=0,
            shrinkB=0,
            connectionstyle="arc3,rad=0.0",  # straight line, no curvature
        )
        fig.patches.append(arrow)

    return fig, axes, G, pos_fig


def plot(
    params: dict[str, Any],
    conn: DuckDBPyConnection,
    history_sql: str,
    config_sql: str,
    success_sql: str,
    sim_data_paths: dict[str, dict[int, str]],
    validation_data_paths: list[str],
    outdir: str,
    variant_metadata: dict[str, dict[int, Any]],
    variant_names: dict[str, str],
):
    os.makedirs(outdir, exist_ok=True)

    query = f"""
        SELECT bulk, time FROM ({history_sql})
        ORDER BY time
    """
    output_queries = conn.sql(query).df()
    bulk_matrix = np.stack(output_queries["bulk"].values).astype(int)
    time_mins = output_queries["time"].values / 60.0

    experiment_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[experiment_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data
    bulk_matrix_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    df_full = pd.DataFrame(bulk_matrix, columns=bulk_matrix_ids)
    df_full["Time (min)"] = time_mins

    base_to_full = {}
    for col in bulk_matrix_ids:
        base = strip_compartment_labels(col)
        base_to_full.setdefault(base, []).append(col)

    df = pd.DataFrame({"Time (min)": time_mins})
    for base, cols in base_to_full.items():
        df[base] = df_full[cols].sum(axis=1).to_numpy()

    rxn_id = "CPLX0-7451_RXN"
    reactants = [
        "G370-MONOMER[i]",
        "G7028-MONOMER[i]",
        "EG11224-MONOMER[j]",
        "EG11975-MONOMER[i]",
        "EG11976-MONOMER[j]",
        "EG11977-MONOMER[i]",
        "G378-MONOMER[c]",
        "EG11656-MONOMER[c]",
        "G377-MONOMER[c]",
    ]
    product = "CPLX0-7451[j]"

    stoich = [
        {
            "id": rxn_id,
            "reactants": {r: 1 for r in reactants},
            "products": {product: 1},
        }
    ]

    nodes = reactants + [product]
    present = [n for n in nodes if n in df.columns]
    missing = [n for n in nodes if n not in df.columns]
    if missing:
        print("WARNING: missing (wont be plotted):", missing)

    timeseries = {n: df[n].to_numpy() for n in present}
    t = df["Time (min)"].to_numpy()

    fig, axes, G, pos = plot_timeseries_network(
        stoich,
        timeseries,
        t=t,
        node_size=0.16,
        title=f"{rxn_id} (reactants left product right)",
        figsize=(14, 8),
        force_left_right_grid=True,
        product_node=product,
        product_size_scale=1.45,
    )

    fig.savefig(
        os.path.join(outdir, f"{rxn_id}_network_timeseries_3x3left.png"),
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)
