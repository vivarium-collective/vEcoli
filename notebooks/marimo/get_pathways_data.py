import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Any, List

import aiohttp
import xmltodict
import pandas as pd


###############################################################################
#  LOGIN
###############################################################################

async def biocyc_login(session: aiohttp.ClientSession, credentials_path: str):
    with open(credentials_path, "r") as f:
        credentials = json.load(f)

    await session.post(
        "https://websvc.biocyc.org/credentials/login/",
        data={"email": credentials["email"], "password": credentials["password"]},
    )


###############################################################################
#  XML HELPERS
###############################################################################

async def fetch_xml(session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore):
    print(f'Fetching xml!')
    """Fetch an XML endpoint (with rate-limit) and parse to dict."""
    async with sem:  # protects BioCyc API from overload
        async with session.get(url) as r:
            text = await r.text()

    try:
        return xmltodict.parse(text)
    except Exception as e:
        print("XML parse error:", url)
        raise


###############################################################################
#  FETCH PATHWAY LIST
###############################################################################

async def fetch_all_pathways(session, sem) -> List[str]:
    print('Fetching all pathways')
    url = "https://websvc.biocyc.org/apixml?fn=get-class-all-instances&id=ECOLI:Pathways&detail=none"

    doc = await fetch_xml(session, url, sem)
    pathways = doc["ptools-xml"]["Pathway"]

    return [p["@frameid"] for p in pathways]


###############################################################################
#  FETCH A SINGLE PATHWAY (REACTIONS)
###############################################################################

async def fetch_pathway(session, sem, pwy_id: str):
    print('Fetching pathway: ', pwy_id)
    url = f"https://websvc.biocyc.org/getxml?ECOLI:{pwy_id}"
    doc = await fetch_xml(session, url, sem)

    pwy = doc["ptools-xml"]["Pathway"]

    entry = {
        "id": pwy_id,
        "name": pwy["common-name"]["#text"],
        "rxns": [],
    }

    rxn_list = pwy["reaction-list"].get("Reaction", None)
    if isinstance(rxn_list, list):
        entry["rxns"] = [rx["@frameid"] for rx in rxn_list]
    elif isinstance(rxn_list, dict):
        entry["rxns"] = [rxn_list["@frameid"]]

    return entry


###############################################################################
#  FETCH GENES OF A SINGLE REACTION
###############################################################################

async def fetch_genes_of_rxn(session, sem, rxn_id: str):
    print(f'Fetching rxn: {rxn_id}')
    url = f"https://websvc.biocyc.org/apixml?fn=genes-of-reaction&id=ECOLI:{rxn_id}"
    doc = await fetch_xml(session, url, sem)

    root = doc["ptools-xml"]
    if "Gene" not in root:
        return []

    genes = root["Gene"]
    if isinstance(genes, list):
        return [g["@frameid"] for g in genes]
    else:
        return [genes["@frameid"]]


###############################################################################
#  FETCH PRODUCTS OF A GENE (COMPOUNDS)
###############################################################################

async def fetch_compounds_of_gene(session, sem, gene_id: str):
    print(f'Fetching compounds of gene: {gene_id}')
    url = f"https://websvc.biocyc.org/apixml?fn=all-products-of-gene&id=ECOLI:{gene_id}"
    doc = await fetch_xml(session, url, sem)

    proteins = doc["ptools-xml"]["Protein"]
    if isinstance(proteins, list):
        return [p["@frameid"] for p in proteins]
    else:
        return [proteins["@frameid"]]


###############################################################################
#  PROCESS ONE PATHWAY (REACTIONS → GENES → COMPOUNDS)
###############################################################################

async def enrich_pathway(session, sem, pwy_entry: Dict[str, Any]):
    print(f'Enriching pathway: {pwy_entry}')
    rxns = pwy_entry["rxns"]

    # Fetch genes for all reactions concurrently
    gene_lists = await asyncio.gather(*[
        fetch_genes_of_rxn(session, sem, rxn) for rxn in rxns
    ])

    pwy_entry["genes"] = []
    pwy_entry["compounds"] = []

    # For each reaction → genes → compounds
    for genes in gene_lists:
        if not genes:
            pwy_entry["genes"].append("")
            pwy_entry["compounds"].append([""])
            continue

        pwy_entry["genes"].append(" // ".join(genes))

        # fetch all compounds in parallel
        cmpds_nested = await asyncio.gather(*[
            fetch_compounds_of_gene(session, sem, g) for g in genes
        ])

        # flatten each reaction’s compound list
        flattened = []
        for lst in cmpds_nested:
            flattened.extend(lst)

        pwy_entry["compounds"].append(flattened)

    return pwy_entry


###############################################################################
#  MAIN
###############################################################################

async def main():
    wd_root = os.getcwd().split("/notebooks")[0]
    dir_credentials = os.path.join(wd_root, "notebooks", "marimo", "credentials")
    wd_out = os.path.join(wd_root, "notebooks", "marimo", "pathways")

    sem = asyncio.Semaphore(10)  # adjustable concurrency

    async with aiohttp.ClientSession() as session:
        # login first
        await biocyc_login(session, os.path.join(dir_credentials, "biocyc_credentials.json"))

        # get list of pathways
        pathways_ids = await fetch_all_pathways(session, sem)
        print(f"Found {len(pathways_ids)} pathways.")

        # fetch all pathways in parallel
        pathways = await asyncio.gather(*[
            fetch_pathway(session, sem, pwy_id) for pwy_id in pathways_ids
        ])

        # enrich (genes + compounds) in parallel
        enriched = await asyncio.gather(*[
            enrich_pathway(session, sem, pwy) for pwy in pathways
        ])

    # -------------------------------------------------------------------------
    # convert to dataframe exactly as before
    # -------------------------------------------------------------------------
    rows = []
    for pwy in enriched:
        name = pwy["name"]
        for rxn, genes, cmpds in zip(pwy["rxns"], pwy["genes"], pwy["compounds"]):
            rows.append({
                "name": name,
                "reactions": rxn,
                "genes": genes,
                "compounds": " // ".join(cmpds),
            })

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(wd_out, "pathways.txt"), sep="\t", index=False)

    print("Saved:", os.path.join(wd_out, "pathways.txt"))


if __name__ == "__main__":
    asyncio.run(main())
