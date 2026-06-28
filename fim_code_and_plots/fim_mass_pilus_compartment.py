# %%
import duckdb
import numpy as np
import pandas as pd
from ecoli.library.sim_data import LoadSimData
import matplotlib.pyplot as plt
import os
from wholecell.utils import units

conn = duckdb.connect()

# Because no real biological changes have been made to simulations, the test installation run is the same as all of them
query_dict = {
    "experiment_id": "test_installation",
    "variant": 0,
    "lineage_seed": 0,
    "generation": 1,
}

query = f"""
    SELECT bulk, listeners__monomer_counts,listeners__mass__pilus_mass, time FROM read_parquet("out/{query_dict["experiment_id"]}/history/*/*/*/*/*/*.pq", hive_partitioning=true)
    WHERE variant={query_dict["variant"]}
    AND lineage_seed={query_dict["lineage_seed"]}
    AND generation={query_dict["generation"]}
    ORDER BY time
"""

# Load sim data
sim_data_default = "reconstruction/sim_data/kb/simData.cPickle"
sim_data1 = LoadSimData(sim_data_default).sim_data

# Translation monomer data
monomer_data = sim_data1.process.translation.monomer_data
monomer_id = monomer_data["id"].tolist()

# Convert column to matrix
out = conn.sql(query).df()
monomer_matrix = np.stack(out["listeners__monomer_counts"].values).astype(int)

# Make DF and get time
monomer = pd.DataFrame(monomer_matrix, columns=monomer_id)
monomer["time_s"] = out["time"].values
time_mins = out["time"].values / 60


# Monomers of interest
monomer_labels = [
    "EG10313-MONOMER[l]",  # FimF
    "EG10314-MONOMER[l]",  # FimG
    "EG10315-MONOMER[l]",  # FimH
]

# Make a copy of the monomer df just extracting the monomers of interest
monomer_fim = monomer[monomer_labels].copy()
monomer_fim["time_mins"] = time_mins

# Extract the molecule weights of monomers of interest
# monomer_fim_mw = monomer_data["mw"].tolist()  #this is too long and all monomers, not ones of interest
# zip is a python function that pairs elements from sequences together - zip(ids,weights) gives a dictionary with the monomers of interest
# This is a dictionary of all ids and molecular weights
monomer_mw = dict(zip(monomer_data["id"], monomer_data["mw"]))

# Molecular weight of FimF, FimG, FimH --> TODO: make spreadsheet similar to flagella stoichiometry to make sure ratios here make sense
fimf_mw = monomer_mw["EG10313-MONOMER[l]"]  # 18,715.311 g/mol
fimg_mw = monomer_mw["EG10314-MONOMER[l]"]  # 17,315.327 g/mol
fimh_mw = monomer_mw["EG10315-MONOMER[l]"]  # 31,474.464 g/mol

# Now convert monomer counts to mass using mw
# Each monomer count is a number of molecules, need to get this into grams
# mass = count x mw / avogadro #
AVOGADRO = 6.022e23
# For loop for each monomer_mw of interest
for fim in monomer_fim.columns:
    if fim != "time_mins":  # because cant get mw of time, so need to exclude
        mw = float(
            monomer_mw[fim].asNumber(units.g / units.mol)
        )  # ensures they're just floats no units
        monomer_fim[fim] = monomer_fim[fim] * (
            mw / AVOGADRO
        )  # each value is now in grams, not counts

# Now can sum across columns to get the total mass of these fim components at each time point and total mass at end of simulation for each column alone
# Plot 1: fim % contribution to the pilus compartment at the end of the cell cycle
# Extract the final row of the monomer_fim df - in g/mol
last_row = monomer_fim.iloc[-1:][
    ["EG10313-MONOMER[l]", "EG10314-MONOMER[l]", "EG10315-MONOMER[l]"]
]

# Initial amount - not right gets all data - not just first row
first_row = monomer_fim.iloc[0:1][
    ["EG10313-MONOMER[l]", "EG10314-MONOMER[l]", "EG10315-MONOMER[l]"]
]


# Extract final pilus mass of compartment at last timepoint
# 1 fg = 1e-15g
final_pilus_mass = out["listeners__mass__pilus_mass"].iloc[-1]
final_pilus_mass_grams = final_pilus_mass * 1e-15


# Percentage of each fim subunits / pilus compartment - Out of the entire pilus mass - what % makes up fimF, fimG, fimG
fim_percents = (last_row / final_pilus_mass_grams * 100).iloc[0]

# Path to folder to save
outdir = "/Users/mayaabdalla/Documents/code/vEcoli/fim_data_plots"

plt.figure(figsize=[8, 8])
fim_bars = plt.bar(
    fim_percents.index, fim_percents.values, color="steelblue", edgecolor="black"
)
for bar in fim_bars:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.1f}%",
        ha="center",
        va="bottom",
        fontsize=12,
    )
plt.ylabel("Contribution to [l] Mass (%)")
plt.title("Fim Subunits Contribution to Pilus Mass (last timepoint)")
plt.grid(axis="y", linestyle="--", alpha=0.6)
plt.tight_layout()
print("Saving to:", os.getcwd())
outfile = os.path.join(
    outdir, "Fim Subunits Contribution to Pilus Mass (last timepoint).png"
)
plt.savefig(outfile, dpi=300)


# Initial pilus monomer amounts - first row only
first_row = monomer_fim.iloc[0:1][
    ["EG10313-MONOMER[l]", "EG10314-MONOMER[l]", "EG10315-MONOMER[l]"]
]

# Initial pilus compartment mass
first_pilus_mass = out["listeners__mass__pilus_mass"].iloc[0]
first_pilus_mass_grams = first_pilus_mass * 1e-15

fim_pilus_first_percent = (first_row / first_pilus_mass_grams * 100).iloc[0]
plt.figure(figsize=[6, 8])
fim_cyt1 = plt.bar(
    fim_pilus_first_percent.index,
    fim_pilus_first_percent.values,
    color="steelblue",
    edgecolor="black",
)
for bar in fim_cyt1:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.6f}%",
        ha="center",
        va="bottom",
        fontsize=12,
    )

plt.ylabel("Contribution to [l] Mass (%)")
plt.title("Fim Subunits Contribution to Pilus Mass (initial timepoint).png")
plt.grid(axis="y", linestyle="--", alpha=0.6)
plt.tight_layout()
print("Saving to:", os.getcwd())
outfile = os.path.join(
    outdir, "Fim Subunits Contribution to Pilus Mass (initial timepoint).png"
)
plt.savefig(outfile, dpi=300)


# Stacked bar graph last_row vs. total pilus mass
fim_masses = last_row.iloc[0].values
fim_labels = ["fimF", "fimG", "fimH"]
fim_total_mass = fim_masses.sum()
reminder_mass = final_pilus_mass_grams - fim_total_mass

# Stacked values : fim subunits and reminder mass
stacked_values = list(fim_masses) + [reminder_mass]
stacked_labels = fim_labels + ["Other pilus masses"]
colors = ["lightcoral", "darkseagreen", "steelblue", "darkgray"]


# plot stacked values
fig, ax = plt.subplots(figsize=[6, 8])
bottom = 0
for value, label, color in zip(stacked_values, stacked_labels, colors):
    ax.bar("Pilus Compartment", value, bottom=bottom, color=color, edgecolor="black")
    ax.text(
        x="Pilus Compartment",
        y=bottom + value / 2,
        s=f"{value:.2e} g ",
        ha="center",
        va="center",
        fontsize=10,
        color="black" if value < final_pilus_mass_grams * 0.15 else "white",
    )
    bottom += value

ax.set_ylabel("Mass (grams)")
ax.set_title("Mass Contribution to Pilus Compartment")
ax.set_ylim(0, final_pilus_mass_grams * 1.1)
ax.legend(loc="upper right", title="Subunit", labels=stacked_labels)
ax.grid(axis="y", linestyle="--", alpha=0.5)
plt.tight_layout()
outfile = os.path.join(
    outdir, "Stacked Barplot Mass Contribution to Pilus Compartment.png"
)
plt.savefig(outfile, dpi=300)
