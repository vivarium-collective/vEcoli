import os
from duckdb import DuckDBPyConnection
import duckdb
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# Loading Simdata to obtain the bulk id labels
from ecoli.library.sim_data import LoadSimData

from cycler import cycler


# Query to select for bulk and time from the test_installation run
conn = DuckDBPyConnection
query_dict = {
    "experiment_id": "test_installation",
    "variant": 0,
    "lineage_seed": 0,
    "generation": 1,
}

# Query to select bulk and time columns only
query = f"""
    SELECT bulk,time FROM read_parquet("out/{query_dict["experiment_id"]}/history/*/*/*/*/*/*.pq", hive_partitioning=true)
    WHERE variant={query_dict["variant"]}
    AND lineage_seed={query_dict["lineage_seed"]}
    AND generation={query_dict["generation"]}
    ORDER BY time
"""

# Order the rows and convert to pandas dataframe
db_bulk = duckdb.sql(query)  # Run DuckRB SQL request
db_bulk = db_bulk.df()

# Convert bulk column to matrix -- a vector of molecule counts at each time step
bulk_state_mtx = np.stack(db_bulk["bulk"].values)

sim_data_default = "reconstruction/sim_data/kb/simData.cPickle"
sim_data = LoadSimData(sim_data_default).sim_data
bulk_molecule_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

# Color palettes
COLORS_256 = [  # From colorbrewer2.org, qualitative 8-class set 1
    [228, 26, 28],
    [55, 126, 184],
    [77, 175, 74],
    [152, 78, 163],
    [255, 127, 0],
    [255, 255, 51],
    [166, 86, 40],
    [247, 129, 191],
]

# RBG colors definitions
COLORS = ["#%02x%02x%02x" % (color[0], color[1], color[2]) for color in COLORS_256]

# Molcules involved in quorum sensing
molecules_qs = [
    "EG12712-MONOMER[c]",
    "G7572-MONOMER[c]",
    "G7575-MONOMER[c]",
    "EG12658-MONOMER[i]",
    "EG11090-MONOMER[c]",
    "EG10320-MONOMER[c]",
    "EG11355-MONOMER[c]",
    "EG10601_RNA[c]",
    "G6798-MONOMER[c]",
    "G6799-MONOMER[c]",
    "EG11447-MONOMER[c]",
]
time_min = (db_bulk["time"] - db_bulk["time"].min()) / 60
qs_df = pd.DataFrame({"Time (min)": time_min})

# Molecules
for molecule in molecules_qs:
    if molecule in bulk_molecule_ids:  # check if molecule exists in the simulation
        idx = bulk_molecule_ids.index(molecule)  # column index of the molecule in bulk
        trajectory = bulk_state_mtx[
            :, idx
        ]  # extract timecourse across all timepoints by the index (idx column of matrix)
        qs_df[molecule] = (
            trajectory / trajectory[0]
        )  # normalize to initial value at time 0
# qs_melted = qs_df.melt(id_vars="Time (min)", var_name="Molecule", value_name="Normalized Abundance")

plt.rcParams["axes.prop_cycle"] = cycler(color=COLORS)


plt.figure(figsize=(12, 6))

for molecule in molecules_qs:
    if molecule in qs_df.columns:
        plt.plot(qs_df["Time (min)"], qs_df[molecule], label=molecule)

plt.xlabel("Time (min)")
plt.ylabel("Normalized Abundance")
plt.title("Quroum Sensing Molecules Dynamics (normalized to 0")
plt.legend(bbox_to_anchor=(1.05, 1), loc=2)
plt.tight_layout()
outdir = "out/plots"
os.makedirs(outdir, exist_ok=True)
plt.savefig(os.path.join(outdir, "quorum_sensing_molecules_plot.png"), dpi=300)
plt.show()
