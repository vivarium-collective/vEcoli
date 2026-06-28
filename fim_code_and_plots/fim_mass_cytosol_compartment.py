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
    SELECT bulk, listeners__monomer_counts,listeners__mass__cytosol_mass, time FROM read_parquet("out/{query_dict["experiment_id"]}/history/*/*/*/*/*/*.pq", hive_partitioning=true)
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


# Monomers --> parts of fim that are in the cytosol
fim_mon = [
    # "EG10308-MONOMER[c]", NOTE:11/13: #why is this not in the index? --> in monomer_id fimA is in tag e = EG10308-MONOMER[e]
    "EG10309-MONOMER[c]",
    "EG10312-MONOMER[c]",
]

# Make a copy of the monomer df just extracting the monomers of interest
monomer_fim2 = monomer[fim_mon].copy()
monomer_fim2["time_mins"] = time_mins

# This is a dictionary of all ids and molecular weights
monomer_mw = dict(zip(monomer_data["id"], monomer_data["mw"]))
AVOGADRO = 6.022e23
for fim in fim_mon:
    if fim != "time_mins":
        mw = float(monomer_mw[fim].asNumber(units.g / units.mol))
        monomer_fim2[fim] = monomer_fim2[fim] * (mw / AVOGADRO)  # now in grams

# The last timepoint for these 2 monomers
last_time_point = monomer_fim2.iloc[-1:][["EG10309-MONOMER[c]", "EG10312-MONOMER[c]"]]


# Cytosol listener total mass --> to grams
final_cytosol_mass = out["listeners__mass__cytosol_mass"].iloc[-1]
final_cytosol_mass_grams = final_cytosol_mass * 1e-15

# Path to folder to save
outdir = "/Users/mayaabdalla/Documents/code/vEcoli/fim_data_plots"


# % fim subunits out of total cytosol mass
fim_cytosol_percents = (last_time_point / final_cytosol_mass_grams * 100).iloc[0]
plt.figure(figsize=[8, 8])
fim_cyt = plt.bar(
    fim_cytosol_percents.index,
    fim_cytosol_percents.values,
    color="steelblue",
    edgecolor="black",
)
for bar in fim_cyt:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.6f}%",
        ha="center",
        va="bottom",
        fontsize=12,
    )
plt.ylabel("Contribution to the [c] Mass (%)")
plt.title("Fim Subunits Contribution to Cytosol Mass (last timepoint).png")
plt.grid(axis="y", linestyle="--", alpha=0.6)
plt.tight_layout()
print("Saving to:", os.getcwd())
outfile = os.path.join(
    outdir, "Fim Subunits Contribution to Cytosol Mass (last timepoint).png"
)
plt.savefig(outfile, dpi=300)


# Cytosol initial values
cyt_first_timepoint = monomer_fim2.iloc[0:1][
    ["EG10309-MONOMER[c]", "EG10312-MONOMER[c]"]
]
first_cyt_mass = out["listeners__mass__cytosol_mass"].iloc[0]
first_cyt_mass_grams = first_cyt_mass * 1e-15  # now in grams

# What % of the cytosol is fim subunits before simulation starts?
cyt_pilus_percent = (cyt_first_timepoint / first_cyt_mass_grams * 100).iloc[0]
# print(cyt_pilus_percent)

plt.figure(figsize=[8, 8])
first_cyt = plt.bar(
    cyt_pilus_percent.index,
    cyt_pilus_percent.values,
    color="skyblue",
    edgecolor="black",
)
for bar in first_cyt:
    height = bar.get_height()
    plt.text(
        bar.get_x() + bar.get_width() / 2,
        height,
        f"{height:.6f}%",
        ha="center",
        va="bottom",
        fontsize=12,
    )

plt.ylabel("Contribution to [c] Mass (%)")
plt.title("Fim Subunits Contribution to Cytosol Mass (initial timepoint).png")
plt.grid(axis="y", linestyle="--", alpha=0.6)
plt.tight_layout()
print("Saving to:", os.getcwd())
outfile = os.path.join(
    outdir, "Fim Subunits Contribution to Cytosol Mass (initial timepoint).png"
)
plt.savefig(outfile, dpi=300)
