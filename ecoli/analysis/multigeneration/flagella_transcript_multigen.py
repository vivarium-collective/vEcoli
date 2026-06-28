import os
import numpy as np
import matplotlib.pyplot as plt
from typing import Any
from duckdb import DuckDBPyConnection

from ecoli.library.sim_data import LoadSimData
from ecoli.library.schema import bulk_name_to_idx

GENE_MAP = ["fliL", "fliE", "fliF", "flgB", "flgA", "flhB", "fliA"]
CISTRON_ID = [
    "EG10322_RNA",
    "EG11346_RNA",
    "EG11347_RNA",
    "G358_RNA",
    "G357_RNA",
    "G7028_RNA",
    "EG11355_RNA",
]
BETA = np.array([1200, 450, 350, 350, 150, 100, 50], dtype=float)
BETA_PRIME = np.array([250, 350, 300, 450, 300, 350, 300], dtype=float)
K_FLHDC = 50
K_FLIA = 600

FLIC_CISTRON_ID = ["EG10321_RNA"]
CLASS3_GENE_MAP = ["fliD", "flgK", "flgL", "motA", "motB", "cheA", "cheW"]
CLASS3_CISTRON_ID = [
    "EG10317_RNA",
    "EG11967_RNA",
    "EG11545_RNA",
    "EG10601_RNA",
    "EG10602_RNA",
    "EG10146_RNA",
    "EG10149_RNA",
]


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
    query = f"""
        SELECT bulk,
               listeners__rna_counts__mRNA_cistron_counts,
               time
        FROM ({history_sql})
        ORDER BY time
    """
    df = conn.sql(query).df()
    make_plot(df, sim_data_paths, outdir)


def make_plot(df, sim_data_paths, outdir):
    df = df.iloc[1:]  # skip t=0 pre-equilibrium point

    exp_id = list(sim_data_paths.keys())[0]
    sim_data_path = list(sim_data_paths[exp_id].values())[0]
    sim_data = LoadSimData(sim_data_path).sim_data

    # Bulk molecule indices
    bulk_mol_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"]
    flhDC_idx = bulk_name_to_idx("CPLX0-3930[c]", bulk_mol_ids)
    fliA_idx = bulk_name_to_idx("EG11355-MONOMER[c]", bulk_mol_ids)
    flgM_idx = bulk_name_to_idx("G369-MONOMER[c]", bulk_mol_ids)
    flgM_fliA_idx = bulk_name_to_idx("FLGM-FLIA-CPLX[c]", bulk_mol_ids)
    flagellum_idx = bulk_name_to_idx("CPLX0-7452[j]", bulk_mol_ids)

    bulk_array = np.stack(df["bulk"].values)
    flhDC_counts = bulk_array[:, flhDC_idx]
    fliA_counts = bulk_array[:, fliA_idx]
    flgM_counts = bulk_array[:, flgM_idx]
    flgM_fliA_counts = bulk_array[:, flgM_fliA_idx]
    flagellum_counts = bulk_array[:, flagellum_idx]

    # K&A gate signals
    X = flhDC_counts / (K_FLHDC + flhDC_counts)
    Y = fliA_counts / (K_FLIA + fliA_counts)
    p_i = (BETA * X[:, np.newaxis] + BETA_PRIME * Y[:, np.newaxis]) / (
        BETA + BETA_PRIME
    )

    # mRNA counts
    trans = sim_data.process.transcription
    cistron_ids = list(trans.cistron_data["id"])
    mrna_count = np.stack(df["listeners__rna_counts__mRNA_cistron_counts"].values)

    classII_indices = [cistron_ids.index(c) for c in CISTRON_ID]
    classII_mrna = mrna_count[:, classII_indices]

    fliC_idx = cistron_ids.index(FLIC_CISTRON_ID[0])
    fliC_mrna = mrna_count[:, fliC_idx]

    class3_indices = [cistron_ids.index(c) for c in CLASS3_CISTRON_ID]
    classIII_mrna = mrna_count[:, class3_indices]

    time_min = df["time"].values / 60
    colors = plt.cm.tab10(np.linspace(0, 1, 7))
    colors3 = plt.cm.Set2(np.linspace(0, 1, len(CLASS3_GENE_MAP)))

    # ── Individual plots ────────────────────────────────────────────────────

    def save(fname):
        plt.tight_layout()
        plt.savefig(os.path.join(outdir, fname), dpi=150)
        plt.close()

    # FlhDC
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, flhDC_counts, color="steelblue", label="FlhDC (CPLX0-3930)")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("FlhDC levels — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flhdc_levels_multigen.png")

    # FliA (free)
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, fliA_counts, color="orange", label="FliA (EG11355-MONOMER)")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("FliA (free) levels — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flia_levels_multigen.png")

    # FlhDC + FliA combined
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, flhDC_counts, color="steelblue", label="FlhDC")
    ax.plot(time_min, fliA_counts, color="orange", label="FliA (free)")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("FlhDC + FliA (free) — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flhdc_flia_combined_multigen.png")

    # Free FlgM
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, flgM_counts, color="firebrick", label="FlgM (G369-MONOMER[c])")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("Free FlgM — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flgm_free_multigen.png")

    # FlgM:FliA complex
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, flgM_fliA_counts, color="purple", label="FLGM-FLIA-CPLX[c]")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("FlgM:FliA sequestration complex — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flgm_flia_complex_multigen.png")

    # Flagellum count
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, flagellum_counts, color="darkgreen", label="CPLX0-7452[j]")
    ax.set_ylabel("Molecule count")
    ax.set_xlabel("Time (min)")
    ax.set_title("Complete flagellum count — multi-generation")
    ax.legend(loc="upper left", fontsize=8)
    save("flagellum_counts_multigen.png")

    # K&A p_i
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, label in enumerate(GENE_MAP):
        ax.plot(time_min, p_i[:, i], color=colors[i], label=label)
    ax.set_ylim(max(0, p_i.min() - 0.05), 1.01)
    ax.set_ylabel("p_i")
    ax.set_xlabel("Time (min)")
    ax.set_title("K&A SUM gate p_i — Class II promoters — multi-generation")
    ax.legend(fontsize=8, loc="upper left")
    save("ka_sum_gate_multigen.png")

    # Y signal
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, Y, color="orange")
    ax.set_ylim(0, 1)
    ax.set_ylabel("Y = FliA/(K_fliA+FliA)")
    ax.set_xlabel("Time (min)")
    ax.set_title(f"FliA activity signal Y (K_fliA={K_FLIA}) — multi-generation")
    save("y_signal_multigen.png")

    # X signal
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, X, color="steelblue")
    ax.set_ylim(0, 1)
    ax.set_ylabel("X = FlhDC/(K_flhDC+FlhDC)")
    ax.set_xlabel("Time (min)")
    ax.set_title(f"FlhDC activity signal X (K_flhDC={K_FLHDC}) — multi-generation")
    save("x_signal_multigen.png")

    # Class II mRNA
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, label in enumerate(GENE_MAP):
        ax.plot(time_min, classII_mrna[:, i], color=colors[i], label=label)
    ax.set_ylabel("mRNA count")
    ax.set_xlabel("Time (min)")
    ax.set_title("Class II mRNA — multi-generation")
    ax.legend(fontsize=8, loc="upper left")
    save("classII_mrna_multigen.png")

    # fliC mRNA
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(time_min, fliC_mrna, color="crimson", label="fliC")
    ax.set_ylabel("mRNA count")
    ax.set_xlabel("Time (min)")
    ax.set_title("fliC mRNA — multi-generation")
    ax.legend(fontsize=8, loc="upper left")
    save("fliC_mrna_multigen.png")

    # Class III mRNA
    fig, ax = plt.subplots(figsize=(9, 4))
    for i, label in enumerate(CLASS3_GENE_MAP):
        ax.plot(time_min, classIII_mrna[:, i], color=colors3[i], label=label)
    ax.set_ylabel("mRNA count")
    ax.set_xlabel("Time (min)")
    ax.set_title("Class III mRNA (fliC excluded) — multi-generation")
    ax.legend(fontsize=8, loc="upper left")
    save("classIII_mrna_multigen.png")

    # ── Big panel (4 × 3) ───────────────────────────────────────────────────
    fig, axes = plt.subplots(4, 3, figsize=(20, 16))
    axes = axes.flatten()

    axes[0].plot(time_min, flhDC_counts, color="steelblue")
    axes[0].set_title("FlhDC levels")
    axes[0].set_ylabel("Count")

    axes[1].plot(time_min, fliA_counts, color="orange")
    axes[1].set_title("FliA (free) levels")
    axes[1].set_ylabel("Count")

    axes[2].plot(time_min, flhDC_counts, color="steelblue", label="FlhDC")
    axes[2].plot(time_min, fliA_counts, color="orange", label="FliA")
    axes[2].set_title("FlhDC + FliA")
    axes[2].legend(fontsize=7)

    for i, label in enumerate(GENE_MAP):
        axes[3].plot(time_min, p_i[:, i], color=colors[i], label=label)
    axes[3].set_ylim(max(0, p_i.min() - 0.05), 1.01)
    axes[3].set_title("K&A SUM gate p_i")
    axes[3].set_ylabel("p_i")
    axes[3].legend(fontsize=6)

    for i, label in enumerate(GENE_MAP):
        axes[4].plot(time_min, classII_mrna[:, i], color=colors[i], label=label)
    axes[4].set_title("Class II mRNA")
    axes[4].set_ylabel("mRNA count")
    axes[4].legend(fontsize=6)

    axes[5].plot(time_min, fliC_mrna, color="crimson", label="fliC")
    axes[5].set_title("fliC mRNA")
    axes[5].set_ylabel("mRNA count")
    axes[5].legend(fontsize=7)

    for i, label in enumerate(CLASS3_GENE_MAP):
        axes[6].plot(time_min, classIII_mrna[:, i], color=colors3[i], label=label)
    axes[6].set_title("Class III mRNA (fliC excluded)")
    axes[6].set_ylabel("mRNA count")
    axes[6].legend(fontsize=6)

    axes[7].plot(time_min, flagellum_counts, color="darkgreen")
    axes[7].set_title("Complete flagellum (CPLX0-7452)")
    axes[7].set_ylabel("Count")

    axes[8].plot(time_min, flgM_counts, color="firebrick")
    axes[8].set_title("Free FlgM (G369-MONOMER)")
    axes[8].set_ylabel("Count")

    axes[9].plot(time_min, flgM_fliA_counts, color="purple")
    axes[9].set_title("FlgM:FliA complex (FLGM-FLIA-CPLX)")
    axes[9].set_ylabel("Count")

    axes[10].plot(time_min, Y, color="orange")
    axes[10].set_ylim(0, 1)
    axes[10].set_title(f"FliA activity Y (K_fliA={K_FLIA})")
    axes[10].set_ylabel("Y = FliA/(K_fliA+FliA)")

    axes[11].plot(time_min, X, color="steelblue")
    axes[11].set_ylim(0, 1)
    axes[11].set_title(f"FlhDC activity X (K_flhDC={K_FLHDC})")
    axes[11].set_ylabel("X = FlhDC/(K_flhDC+FlhDC)")

    for ax in axes:
        ax.set_xlabel("Time (min)")

    plt.suptitle(
        "Flagella Transcription Cascade — Multi-generation Overview",
        fontsize=13,
        y=1.01,
    )
    plt.tight_layout()
    plt.savefig(
        os.path.join(outdir, "flagella_panel_multigen.png"),
        dpi=150,
        bbox_inches="tight",
    )
    plt.close()
