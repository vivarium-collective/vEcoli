# This script generates a network plot with mini timeseries plots
# for the final flagellum reaction (CPLX0-7452_RXN),
# using gene names instead of EcoCyc molecule IDs.

import os
from typing import Any

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.transforms import Bbox
from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData


def strip_compartment(mol_id: str) -> str:
    return mol_id


def left_right_star_layout_grid(
    reactants,
    product,
    *,
    left_x_range=(0.08, 0.50),
    y_range=(0.72, 0.42),
    ncols=3,
    nrows=2,
    right_x=0.75,
    product_y=0.57,
):
    reactants = sorted(list(reactants))
    x0, x1 = left_x_range
    y_top, y_bot = y_range
    xs = np.linspace(x0, x1, ncols)
    ys = np.linspace(y_top, y_bot, nrows)
    pos = {}

    for i, r in enumerate(reactants):
        row = i // ncols
        col = i % ncols
        pos[r] = (float(xs[col]), float(ys[row]))

    pos[product] = (float(right_x), float(product_y))
    return pos


def rect_edge_intersection(src_xy, dst_xy, rect):
    x0, y0, x1, y1 = rect
    xA, yA = src_xy
    xB, yB = dst_xy
    dx, dy = xB - xA, yB - yA
    eps = 1e-12
    candidates = []

    if abs(dx) > eps:
        t = (x0 - xA) / dx
        y = yA + t * dy
        if 0 < t < 1 and y0 <= y <= y1:
            candidates.append((t, (x0, y)))
        t = (x1 - xA) / dx
        y = yA + t * dy
        if 0 < t < 1 and y0 <= y <= y1:
            candidates.append((t, (x1, y)))

    if abs(dy) > eps:
        t = (y0 - yA) / dy
        x = xA + t * dx
        if 0 < t < 1 and x0 <= x <= x1:
            candidates.append((t, (x, y0)))
        t = (y1 - yA) / dy
        x = xA + t * dx
        if 0 < t < 1 and x0 <= x <= x1:
            candidates.append((t, (x, y1)))

    if not candidates:
        return (xA, yA)

    candidates.sort(key=lambda z: z[0])
    return candidates[0][1]


def ax_center_in_figcoords(ax_):
    bb: Bbox = ax_.get_position()
    return (bb.x0 + bb.width / 2, bb.y0 + bb.height / 2)


def ax_rect_in_figcoords(ax_):
    bb: Bbox = ax_.get_position()
    return (bb.x0, bb.y0, bb.x1, bb.y1)


def plot_stoich_timeseries_network(
    stoich,
    timeseries,
    t=None,
    *,
    node_size=0.16,
    arrowstyle="->",
    arrowsize=12,
    edge_alpha=0.55,
    edge_width=1.3,
    sharex=True,
    sharey=False,
    ypad_frac=0.05,
    title=None,
    fig=None,
    figsize=(14, 8),
    force_left_right_grid=True,
    product_node=None,
    product_size_scale=1.00,
):
    def build_graph_from_reaction_list(rxns):
        G = nx.DiGraph()
        for sp in timeseries.keys():
            G.add_node(sp)
        for r in rxns:
            reactants = list((r.get("reactants") or {}).keys())
            products = list((r.get("products") or {}).keys())
            for sp in reactants + products:
                if sp in timeseries and not G.has_node(sp):
                    G.add_node(sp)
            for a in reactants:
                for b in products:
                    if a in timeseries and b in timeseries and a != b:
                        G.add_edge(a, b)
        return G

    if not isinstance(stoich, (list, tuple)):
        raise ValueError(
            "This script expects stoich as a reaction-list: [{'reactants':..., 'products':...}]."
        )

    G = build_graph_from_reaction_list(stoich)
    if G.number_of_nodes() == 0:
        raise ValueError("Graph has no nodes after filtering to timeseries keys.")

    keys = list(G.nodes)
    arrays = []
    min_len = None
    for k in keys:
        y = np.asarray(timeseries[k], dtype=float).ravel()
        arrays.append(y)
        min_len = len(y) if min_len is None else min(min_len, len(y))
    arrays = [y[:min_len] for y in arrays]

    if t is None:
        t = np.arange(min_len, dtype=float)
    else:
        t = np.asarray(t, dtype=float).ravel()[:min_len]

    xlim = (float(np.min(t)), float(np.max(t))) if sharex else None

    if sharey:
        all_y = np.concatenate(arrays) if arrays else np.array([0.0])
        ylo, yhi = float(np.min(all_y)), float(np.max(all_y))
        if ylo == yhi:
            ylo -= 1.0
            yhi += 1.0
        ylim = (ylo, yhi)
    else:
        ylim = None

    pos_fig = None
    inferred_product = None

    if force_left_right_grid and len(stoich) == 1:
        r0 = stoich[0]
        inferred_reactants = [
            k for k in (r0.get("reactants") or {}).keys() if k in G.nodes
        ]
        inferred_products = [
            k for k in (r0.get("products") or {}).keys() if k in G.nodes
        ]

        if product_node is not None and product_node in G.nodes:
            inferred_product = product_node
        elif len(inferred_products) == 1:
            inferred_product = inferred_products[0]

        if inferred_product is not None:
            pos_fig = left_right_star_layout_grid(
                inferred_reactants,
                inferred_product,
                left_x_range=(0.08, 0.50),
                y_range=(0.72, 0.42),
                ncols=3,
                nrows=2,
                right_x=0.75,
                product_y=0.57,
            )

    if pos_fig is None:
        pos = nx.spring_layout(G, seed=1)
        xs = np.array([pos[n][0] for n in G.nodes], dtype=float)
        ys = np.array([pos[n][1] for n in G.nodes], dtype=float)
        xs = (xs - xs.min()) / (xs.max() - xs.min() + 1e-12)
        ys = (ys - ys.min()) / (ys.max() - ys.min() + 1e-12)
        pos_fig = {n: (float(x), float(y)) for n, x, y in zip(G.nodes, xs, ys)}

    if fig is None:
        fig = plt.figure(figsize=figsize)
    else:
        fig.clf()

    if title:
        fig.suptitle(title, x=0.48)

    axes = {}
    product_for_size = product_node or inferred_product

    for k in G.nodes:
        x, y = pos_fig[k]
        size = node_size * (
            product_size_scale
            if (product_for_size is not None and k == product_for_size)
            else 1.0
        )
        left = min(max(x - size / 2, 0.0), 1.0 - size)
        bottom = min(max(y - size / 2, 0.0), 1.0 - size)
        ax = fig.add_axes([left, bottom, size, size])
        axes[k] = ax

    for k, y in zip(keys, arrays):
        ax = axes[k]
        ax.plot(t, y, linewidth=1.5)
        ax.set_title(k, fontsize=8, pad=2)

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

        if k != product_for_size:
            ax.set_xticks([])
            ax.set_xticklabels([])
        else:
            ax.set_xlabel("Time (min)", fontsize=7)

        ax.tick_params(labelsize=6, length=2)
        for spine in ax.spines.values():
            spine.set_linewidth(0.8)

    # Arrows are intentionally left commented out
    # for u, v in G.edges():
    #     cu = ax_center_in_figcoords(axes[u])
    #     cv = ax_center_in_figcoords(axes[v])
    #     ru = ax_rect_in_figcoords(axes[u])
    #     rv = ax_rect_in_figcoords(axes[v])
    #     start = rect_edge_intersection(cu, cv, ru)
    #     end = rect_edge_intersection(cv, cu, rv)
    #     arrow = FancyArrowPatch(
    #         start,
    #         end,
    #         transform=fig.transFigure,
    #         arrowstyle=arrowstyle,
    #         mutation_scale=arrowsize,
    #         linewidth=edge_width,
    #         alpha=edge_alpha,
    #         shrinkA=0,
    #         shrinkB=0,
    #         connectionstyle="arc3,rad=0.0",
    #     )
    #     fig.patches.append(arrow)

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

    # Map assembly-relevant bulk species IDs -> clean gene names
    species_to_gene_name = {
        "FLAGELLAR-MOTOR-COMPLEX[j]": "MOTOR_COMPLEX",
        "G361-MONOMER[c]": "FLGE",
        "EG11967-MONOMER[e]": "FLGK",
        "EG11545-MONOMER[e]": "FLGL",
        "EG10321-MONOMER[e]": "FLIC",
        "EG10841-MONOMER[e]": "FLID",
        "CPLX0-7452[j]": "FLAGELLUM",
    }

    rxn_id = "CPLX0-7452_RXN"

    reactant_species = [
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "G361-MONOMER[c]",  # FLGE
        "EG11967-MONOMER[e]",  # FLGK
        "EG11545-MONOMER[e]",  # FLGL
        "EG10321-MONOMER[e]",  # FLIC
        "EG10841-MONOMER[e]",  # FLID
    ]
    product_species = "CPLX0-7452[j]"

    df = pd.DataFrame({"Time (min)": time_mins})

    missing_species = []
    for species_id in reactant_species + [product_species]:
        if species_id not in df_full.columns:
            missing_species.append(species_id)
            continue
        gene_name = species_to_gene_name[species_id]
        df[gene_name] = df_full[species_id].to_numpy()

    if missing_species:
        print("WARNING: these bulk species were not found and won't be plotted:")
        for sp in missing_species:
            print(f"  {sp}")

    reactants = [
        species_to_gene_name[sp] for sp in reactant_species if sp in df_full.columns
    ]
    product = species_to_gene_name[product_species]

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
        print("WARNING: missing gene-name nodes (won't be plotted):", missing)

    timeseries = {n: df[n].to_numpy() for n in present}
    t = df["Time (min)"].to_numpy()

    fig, axes, G, pos = plot_stoich_timeseries_network(
        stoich,
        timeseries,
        t=t,
        node_size=0.16,
        title="Final Flagellum Assembly Protein Counts",
        figsize=(14, 8),
        force_left_right_grid=True,
        product_node=product,
        product_size_scale=1.00,
    )

    if product in axes:
        axes[product].set_title("Flagellum", fontsize=9, pad=2)

    plt.subplots_adjust(left=0.05, right=0.98, top=0.90, bottom=0.08)

    # fig.text(0.02, 0.5, "Molecule Count", va="center", rotation="vertical")
    fig.savefig(
        os.path.join(outdir, f"{rxn_id}_network_timeseries_gene_names.png"),
        dpi=300,
        bbox_inches="tight",
    )
    plt.close(fig)
