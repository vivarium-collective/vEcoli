# %%
import duckdb
import pandas as pd
import numpy as np
from ecoli.library.sim_data import LoadSimData
from ecoli.library import units
import matplotlib.pyplot as plt

conn = duckdb.connect()

query_dict = {
    "experiment_id": "test_installation",
    "variant": 0,
    "lineage_seed": 0,
    "generation": 1,
}

query = f"""
    SELECT bulk, 
    listeners__mass__extracellular_mass, 
    listeners__mass__periplasm_mass, 
    listeners__mass__cytosol_mass,
    listeners__mass__pilus_mass,
    listeners__mass__outer_membrane_mass,
    listeners__mass__flagellum_mass,
    listeners__mass__projection_mass,
    listeners__mass__membrane_mass,
    listeners__mass__inner_membrane_mass,
    time FROM read_parquet("out/{query_dict["experiment_id"]}/history/*/*/*/*/*/*.pq", hive_partitioning=true)
    WHERE variant={query_dict["variant"]}
    AND lineage_seed={query_dict["lineage_seed"]}
    AND generation={query_dict["generation"]}
    ORDER BY time
"""


# Sim data for labels
sim_data = "reconstruction/sim_data/kb/simData.cPickle"
sim_data1 = LoadSimData(sim_data).sim_data
bulk_ids = sim_data1.internal_state.bulk_molecules.bulk_data["id"].tolist()
# bulk_masses = sim_data1.getter.get_mass('EG10310-MONOMER[p]')

# Convert bulk column to a matrix
out = conn.sql(query).df()
time_to_mins = out["time"].values / 60
bulk_matrix1 = np.stack(out["bulk"].values).astype(int)

# Creating a df with labels and time
bulk_df1 = pd.DataFrame(bulk_matrix1, columns=bulk_ids)
bulk_df1["Time (min)"] = time_to_mins

# bulk_df1.to_csv("bulk_df1.csv")

# Copy df to make changes to new one
bulk_df1_copy = bulk_df1.copy()
bulk_df1_copy = bulk_df1_copy.astype(float)

# For loop so don't do this manually
flagella_monomers = [
    "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
    "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
    "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
    "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
    "FLGH-FLAGELLAR-L-RING[j]",
    "FLGI-FLAGELLAR-P-RING[j]",
    "FLIF-FLAGELLAR-MS-RING[i]",
    "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
    "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
    "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
]

monomers_from_bulk = [
    "EG10308-MONOMER[e]",
    "EG10309-MONOMER[c]",
    "EG10310-MONOMER[p]",
    "EG10311-MONOMER[o]",
    "EG10312-MONOMER[c]",
    "EG10313-MONOMER[l]",
    "EG10314-MONOMER[l]",
    "EG10315-MONOMER[l]",
]


# Compartment tags
tags = ("[j]", "[c]", "[e]", "[s]", "[m]", "[o]", "[p]", "[l]", "[i]")

listener_tags = {
    "[j]": "listeners__mass__projection_mass",
    "[c]": "listeners__mass__cytosol_mass",
    "[e]": "listeners__mass__extracellular_mass",
    "[s]": "listeners__mass__flagellum_mass",
    "[m]": "listeners__mass__membrane_mass",
    "[o]": "listeners__mass__outer_membrane_mass",
    "[p]": "listeners__mass__periplasm_mass",
    "[l]": "listeners__mass__pilus_mass",
    "[i]": "listeners__mass__inner_membrane_mass",
}


groups = {
    "fimbriae": monomers_from_bulk,
    "flagella": flagella_monomers,
    # "remaining": listener_tags,
}


monomers_of_interest = monomers_from_bulk + flagella_monomers
filtered_df = bulk_df1_copy.filter(items=monomers_of_interest)

# monomer counts at time 0
filtered_df_time_zero = filtered_df.loc[0]
# print(filtered_df_time_zero)

# Monomers to get molecular weights
monomer_data = sim_data1.process.translation.monomer_data
monomer_weights = dict(zip(monomer_data["id"], monomer_data["mw"]))

# Listener_masses mapped to compartments
out_copy = out.copy()
out_first_row = out_copy.iloc[0]  # first row of listeners


listener_masses = {}

for tag, listener_label in listener_tags.items():
    if listener_label in out_first_row.index:
        mass_value = float(out_first_row[listener_label])
        mass_values_grams = mass_value * 1e-15  # convert fg to grams
        listener_masses[tag] = {
            "listener_label": listener_label,
            "mass_value": mass_values_grams,
        }
    # print(listener_tags[tag])

compartment_masses = {}
masses = {}
compartments = ("[j]", "[c]", "[e]", "[s]", "[m]", "[o]", "[p]", "[l]", "[i]")
AVOGADRO = 6.022e23
# For loop #1: outermost dictionary - compartments
# tags to tag
# change groups to all the same types

for tags in compartments:
    compartment_masses[tags] = {}
    for group_name, group_ids in groups.items():  # creating nested dicts
        compartment_masses[tags][
            group_name
        ] = {}  # empty dict with flagella and fimbriae and remaining in each tag
        for molecule_id in group_ids:
            if (
                molecule_id.endswith(tags)
                and molecule_id in filtered_df_time_zero.index
            ):
                mol_counts = float(filtered_df_time_zero.loc[molecule_id])
                mw_values = monomer_weights.get(molecule_id)
                if mw_values is None:
                    continue
                molecular_mass = mol_counts * (mw_values / AVOGADRO).asNumber(
                    units.g / units.mol
                )
                compartment_masses[tags][group_name][molecule_id] = molecular_mass

category_sums = {}
for tag, subgroups in compartment_masses.items():
    category_sums[tag] = {}
    for group_name, molecules in subgroups.items():
        unitless_masses = molecules.values()
        total_mass = float(sum(unitless_masses))
        category_sums[tag][group_name] = total_mass

    # TODO: generalize this into loop instead of key directly
    fim_mass = category_sums[tag].get("fimbriae", 0)
    flagella_mass = category_sums[tag].get("flagella", 0)

    listener_mass = listener_masses[tag]["mass_value"]
    remaining_mass = listener_mass - (fim_mass + flagella_mass)
    category_sums[tag]["remaining_mass"] = remaining_mass
print(category_sums)


# converting the nested dict category_sums to a pandas dataframe for plotting
df_sums = pd.DataFrame.from_dict(category_sums, orient="index")

# just these 3 columns for now
df_remaining = df_sums[["fimbriae", "flagella", "remaining_mass"]]
print(df_remaining.columns)

# some values were way too tiny to see so need to normalize each bar on itself
# composition not scale
# axis=1 - go across columns and sum each row and then divide the original df by summed rows, axis=0 to attach by rows
# one way to do it but how can I do this and ensure the data is still scaled?
# this normalized to each compartment but the scale is important too
# everything adds to 1 in this case but don't want that
df_remaining_normalized = df_remaining.div(df_remaining.sum(axis=1), axis=0)

ax = df_remaining_normalized.plot(
    kind="bar",
    stacked=True,
    color=["lightblue", "yellowgreen", "salmon"],
    edgecolor="black",
    figsize=(12, 8),
    ylabel="Mass in grams (log scale)",
    title="compartment masses by compartment",
)

ax.set_yscale("log")
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig("Fim and Flagella by Compartment.png")


# Broken y-axis to see values across 2 scales
fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(12, 8))
df_remaining.plot(
    kind="bar",
    stacked=True,
    color=["lightblue", "yellowgreen", "salmon"],
    edgecolor="black",
    ax=ax1,
)

df_remaining.plot(
    kind="bar",
    stacked=True,
    color=["lightblue", "yellowgreen", "salmon"],
    edgecolor="black",
    ax=ax2,
    legend=False,
)

ax1.set_yscale("log")
ax1.set_ylim(1e-16, 1e-11)
ax1.spines["bottom"].set_visible(False)

ax2.set_yscale("log")
ax2.set_ylim(1e-20, 1e-17)
ax2.spines["top"].set_visible(False)

d = 0.5
kwargs = dict(
    marker=[(-1, -d), (1, d)],
    markersize=12,
    linestyle="none",
    color="black",
    mec="k",
    mew=1,
    clip_on=False,
)
ax1.plot([0, 1], [0, 0], transform=ax1.transAxes, **kwargs)
ax1.legend(loc="upper right", frameon=True)
ax2.plot([0, 1], [1, 1], transform=ax2.transAxes, **kwargs)
plt.xticks(rotation=0)
plt.tight_layout()
plt.savefig("Fim and Flagella by Compartment Stacked Barplot.png")
