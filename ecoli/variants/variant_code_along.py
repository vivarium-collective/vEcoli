"""
Copied the new_gene_internal_shift.py script, so I can make comments and learn variants

No real changes been made - wanted to mark up the file and learn how variants work and data structures used here

"""

from typing import Any, cast, TYPE_CHECKING

from ecoli.variants.condition import apply_variant as condition_variant

if TYPE_CHECKING:
    from reconstruction.ecoli.simulation_data import SimulationDataEcoli


def get_new_gene_ids(
    sim_data: "SimulationDataEcoli",
) -> tuple[list[str], list[int], list[str], list[int]]:  # returns 4-element tuple

    cistron_sim = sim_data.process.transcription.cistron_data.struct_array
    monomer_sim = sim_data.process.translation.monomer_data.struct_array

    # names of new gene cistrons
    new_gene_cistrons = cast(
        list[str], cistron_sim[cistron_sim["is_new_gene"]]["id"].tolist()
    )

    # dict(zip[]) to combine two lists or intercoms into a single dict
    cistron_monomers_dict = dict(zip(monomer_sim["cistron_id"], monomer_sim["id"]))

    # cast converts a value of data type to another type - returns it unchanged too
    new_monomer_ids = [
        cast(str, cistron_monomers_dict.get(cistron_id))
        for cistron_id in new_gene_cistrons
    ]

    if len(new_gene_cistrons) == 0:
        raise Exception(
            "This variant runs on simulations,"
            "where new gene option was enable but no new gene cistrons were found"
        )

    if len(new_monomer_ids) == 0:
        raise Exception(
            "This variant runs on simulations,"
            "where new gene option was enabled but no new gene proteins where found "
        )
    assert len(new_monomer_ids) == len(new_gene_cistrons), (
        "number of new gene monomers and cistrons should be equal"
    )

    rna_data = sim_data.process.transcription.rna_data

    # Python splicing + dictionary comprehension
    # string[start : end] if you leave out the start, it starts from the beginning
    # -3 (negative number so counts from the end)
    # so this, gets an id from rna_data (ex. EX10383_RNA then returns EX10383)
    # i for i - indexing, enumerate gives index number - enumerate lets you loop over something and get
    # its index at the same time

    cistron_ids_dicts = {rna[:-3]: i for i, rna in enumerate(rna_data["id"])}

    new_gene_indeces = [
        cast(int, cistron_ids_dicts.get(cistron_id)) for cistron_id in new_gene_cistrons
    ]

    monomer_idx_dict = {monomer: i for i, monomer in enumerate(monomer_sim["id"])}
    new_monomer_indices = [
        cast(int, monomer_idx_dict.get(monomer_id)) for monomer_id in new_monomer_ids
    ]

    return new_gene_cistrons, new_gene_indeces, new_monomer_ids, new_monomer_indices


def modify_gen_exp(
    sim_data: "SimulationDataEcoli", expression: float, translation_efficiency: float
):

    _, new_gene_ideces, _, new_monomer_indices = get_new_gene_ids(sim_data)

    for gene_id, monomer_ids in zip(new_gene_ideces, new_monomer_indices):
        sim_data.adjust_new_gene_final_expression([gene_id], [expression])
        sim_data.process.translation.efficiencies_by_monomer[monomer_ids] = (
            translation_efficiency
        )


def apply_variant(
    sim_data: "SimulationDataEcoli", params: dict[str, Any]
) -> "SimulationDataEcoli":

    # set media condition
    sim_data = condition_variant(sim_data, params)

    # internlize internal shift dict
    sim_data.internal_shift_dict = {}

    # Add the new gene induction to the internal_shift instructions
    induction_gene = params.get("induction_gene", 1)
    sim_data.internal_shift_dict[induction_gene] = (
        modify_gen_exp(params["exp_trl_eff"]["exp"], params["exp_trl_eff"]["trl_eff"]),
    )
    if "knockout_gen" in params:
        assert params["knockout_gene"] > induction_gene, (
            "knockout gene must be after induction gene"
        )
        sim_data.internal_shift_dict[params["knockout_gen"]] = (  # type: ignore[attr-defined]
            modify_gen_exp,
            (0, params["exp_trl_eff"]["trl_eff"]),
        )

    return sim_data
