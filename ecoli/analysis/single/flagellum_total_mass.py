from typing import Any
import os
from duckdb import DuckDBPyConnection
from ecoli.library.sim_data import LoadSimData
import pandas as pd
import numpy as np


""" 
This script is to calculate the flagellum total masses, all of the monomers involved in flagella, 
makes a table for the final count of that protein and then gets the molecular weight in a column too 
"""


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
    with open(os.path.join(outdir, "history_sql.txt"), "w") as f:
        f.write(history_sql)

    query = f"""
        SELECT bulk,time FROM ({history_sql})
        ORDER BY time
    """

    query_df = conn.sql(query).df()

    exp_ids = list(sim_data_paths.keys())[0]
    sim_data_values = list(sim_data_paths[exp_ids].values())[0]
    sim_data = LoadSimData(sim_data_values).sim_data
    sim_data_ids = sim_data.internal_state.bulk_molecules.bulk_data["id"].tolist()

    ids_as_array = np.stack(query_df["bulk"].values).astype(int)
    time_mins = query_df["time"].values / 60

    bulk_df = pd.DataFrame(ids_as_array, columns=sim_data_ids)
    bulk_df["time"] = time_mins

    flagella_rxn_monomers = [
        "G370-MONOMER[i]",  # FlhA
        "G7028-MONOMER[i]",  # FlhB
        "EG11224-MONOMER[j]",  # FliO
        "EG11975-MONOMER[i]",  # FliP
        "EG11976-MONOMER[j]",  # FliQ THIS IS WRONG AND SHOULD BE [i]
        "EG11977-MONOMER[i]",  # FliR
        "EG11656-MONOMER[c]",  # FliH
        "G377-MONOMER[c]",  # FliI
        "G378-MONOMER[c]",  # FliJ
        "CPLX0-7451[j]",
        "FLIF-FLAGELLAR-MS-RING[i]",
        "EG11346-MONOMER[p]",  # FliE
        "FLIG-FLAGELLAR-SWITCH-PROTEIN[i]",
        "FLIM-FLAGELLAR-C-RING-SWITCH[i]",
        "FLIN-FLAGELLAR-C-RING-SWITCH[m]",
        "FLGB-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGC-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGF-FLAGELLAR-MOTOR-ROD-PROTEIN[j]",
        "FLGG-FLAGELLAR-MOTOR-ROD-PROTEIN[o]",
        "FLGI-FLAGELLAR-P-RING[j]",
        "FLGH-FLAGELLAR-L-RING[j]",  # should this be [o]
        "FLAGELLAR-MOTOR-COMPLEX[j]",
        "EG10322-MONOMER[j]",  # FliL -  should be p?
        "MOTA-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "MOTB-FLAGELLAR-MOTOR-STATOR-PROTEIN[i]",
        "G361-MONOMER[c]",  # flgE
        "EG11967-MONOMER[e]",  # FlgK
        "EG11545-MONOMER[e]",  # FlgL
        "EG10321-MONOMER[e]",  # FliC
        "EG10841-MONOMER[e]",  # FliD
        "CPLX0-7452[j]",
    ]

    # remove the compartment labels to get the common names (otherwise dont match)
    def strip_compartment(mol_id):
        return mol_id.split("[")[0]

    def many_common_names(ids: list[str]):
        results = {}
        for monomer_id in ids:
            base_id = strip_compartment(monomer_id)
            common_name = sim_data.common_names.get_common_name(base_id)
            results[base_id] = common_name
        return results

    common_name_dict = many_common_names(flagella_rxn_monomers)

    final_row = bulk_df.iloc[-1]
    first_row = bulk_df.iloc[0]
    rows = []
    n_avogadro = sim_data.constants.n_avogadro.asNumber()

    for mol_id in flagella_rxn_monomers:
        if mol_id in bulk_df.columns:
            first_count = int(first_row[mol_id])
            final_count = int(final_row[mol_id])
            molecular_weight = sim_data.getter.get_mass(mol_id).asNumber()

            base_id = strip_compartment(mol_id)
            common_name_data = common_name_dict.get(base_id, base_id)

            # final mass of each monomer based on end of simulation counts and MW in fg
            final_mass_fg = final_count * molecular_weight / n_avogadro * 1e15

            # initial mass too in fg
            initial_mass_fg = first_count * molecular_weight / n_avogadro * 1e15

            rows.append(
                {
                    "molecule": mol_id,
                    "common_name": common_name_data,
                    "first_count": first_count,
                    "final_count": final_count,
                    "molecular_weight": molecular_weight,
                    "initial_mass_fg": initial_mass_fg,
                    "final_mass_fg": final_mass_fg,
                }
            )

    flg_mass_table = pd.DataFrame(rows)

    flg_mass_table.to_csv(
        os.path.join(outdir, "flagella_monomer_counts_and_masses.csv"),
        index=False,
    )
