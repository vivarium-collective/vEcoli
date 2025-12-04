import marimo

__generated_with = "0.17.7"
app = marimo.App(width="full")


@app.cell
def _():
    from pathlib import Path 
    import marimo as mo
    from ecoli.experiments.ecoli_master_sim import EcoliSim, CONFIG_DIR_PATH
    return CONFIG_DIR_PATH, EcoliSim, Path


@app.cell
def _(CONFIG_DIR_PATH, EcoliSim, Path):
    configpath = Path(CONFIG_DIR_PATH) / "wcecoli_figure2_setD4.json"
    sim = EcoliSim.from_file(configpath)
    return (sim,)


@app.cell
def _(sim):
    sim
    return


@app.cell
def _(sim):
    sim.sim_data_path = '/Users/alexanderpatrie/sms/vecoli_fork/data/wcecoli_figure2_setD4/parca/kb/simData.cPickle'
    sim.build_ecoli()
    return


@app.cell
def _(sim):
    sim.max_duration = 22
    sim.run()
    return


@app.cell
def _():
    # idea: generate vector embedding for sim outputs, store in vector DB, then perform similarity searches for optimizations AND also perform sensitivity analysis this way!
    # all_vecs = list(map(lambda yt: nested_dict_to_embedding(yt), loaded_row_dicts))
    # sigma([v_t for v_t in all_vecs])

    import json
    from collections.abc import Mapping, Iterable
    from typing import Any

    # Example placeholder embedding function.
    # Replace with your actual embedding model (OpenAI, HuggingFace, etc.)
    def embed_text(text: str) -> list[float]:
        import numpy as np
        # Dummy embedding for demonstration; replace with real model output
        return np.random.normal(size=256).tolist()


    def canonicalize(x: Any):
        """
        Convert arbitrarily nested dicts/lists/heterogeneous types into a stable,
        JSON-serializable structure with a deterministic order.
        """
        # Handle dicts
        if isinstance(x, Mapping):
            return {
                str(k): canonicalize(v)
                for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))
            }

        # Handle lists/tuples/sets
        if isinstance(x, Iterable) and not isinstance(x, (str, bytes)):
            # Keep original order for lists/tuples.
            # Convert sets to sorted lists for determinism.
            if isinstance(x, set):
                return [canonicalize(v) for v in sorted(x, key=lambda v: str(v))]
            return [canonicalize(v) for v in x]

        # Primitive types
        if isinstance(x, (str, int, float, bool)) or x is None:
            return x

        # Fallback for custom objects → string representation
        return str(x)


    def nested_dict_to_embedding(data: dict) -> list[float]:
        """
        Convert arbitrarily nested Python dict to a vector embedding.
        Steps:
        1. Canonicalize structure deterministically
        2. Serialize as canonical JSON
        3. Use text embedding model on resulting string
        """
        canonical = canonicalize(data)
        json_str = json.dumps(canonical, indent=None, sort_keys=True)
        vector = embed_text(json_str)
        return vector
    return json, nested_dict_to_embedding


@app.cell
def _(json, nested_dict_to_embedding):
    with open('/Users/alexanderpatrie/sms/vecoli_fork/data/vivecoli_t2526.json', 'r') as fp:
        y_i = json.load(fp)

    embedding = nested_dict_to_embedding(y_i)
    return (embedding,)


@app.cell
def _(embedding):
    embedding
    return


@app.cell
def _(Path, nested_dict_to_embedding):
    import polars as pl

    embeddings = {}
    for r, _, files in Path('/Users/alexanderpatrie/sms/vecoli_fork/out/sms_multiseed_multigen/history/experiment_id=sms_multiseed_multigen/variant=0').walk():
        for f in files:
            filepath = r / f 
            variant, seed, gen, agent = list(map(
                lambda part: part.split("=")[-1],
                filepath.parts[9:13]
            ))
            variant, seed, gen = list(map(
                lambda v: int(v),
                [variant, seed, gen]
            ))
            print(filepath, variant, seed, gen, agent)
            key = ":".join(filepath.parts[9:13])
            embedding_f = nested_dict_to_embedding(pl.read_parquet(filepath).to_pandas().to_dict())
            embeddings[filepath.parts[-1].replace('.pq', '')] = embedding_f
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
