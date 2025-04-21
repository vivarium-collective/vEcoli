"""
Bigraph Workflow: New workflow runner to replace nextflow, allowing for parameterization via JSON

NOTE: this is very analogous to processing API requests and a common thread will be drawn here.
TODO: Extract API functionality/structure from this workflow and visa-versa
"""
from dataclasses import dataclass, field, asdict
import json
import logging
import os
import uuid

import typer
import numpy as np

from vivarium.vivarium import Vivarium
from ecoli import ecoli_core


class SimulationId:
    def __new__(cls, name: str | None = None):
        return f"{name or 'noname'}-{uuid.uuid4()}"


class AuthenticationError(Exception):
    pass


logger = logging.getLogger(__name__)
cli = typer.Typer()


def new_vivarium(doc_path: str):
    with open(doc_path, 'r') as f:
        document = json.load(f)
    return Vivarium(document=document, core=ecoli_core, processes=ecoli_core.processes, types=ecoli_core.types())


def check_key(key):
    return True 

    
@cli.command()
def run(
    config_path: str, 
    out_dir: str,
    total_time: float | None = None, 
    experiment_id: str | None = None, 
    time_step: float = 1.0
):
    """
    :param config_path: (str) Path to the configuration JSON file
    :param total_time: (float) Sim duration
    :param time_step: (float) Sim timestep
    """
    
    with open(config_path, "r") as f:
        config = json.load(f)

    doc_path = config.get("composite_path")
    duration: float = total_time or config.get("total_time", 11.0)
    logger.info(f"Launching a new vivarium simulation with duration: {total_time}")

    # new instance
    viv = new_vivarium(doc_path)

    # check emitter
    if "emitter" not in viv.get_state().keys():
        viv.add_emitter()
    
    # run
    viv.run(duration)

    # collect and save results as json
    results = viv.get_results()

    simulation_id = experiment_id or SimulationId()
    results_fp = os.path.join(out_dir, f"{simulation_id}.json")
    with open(results_fp, "w") as fp:
        json.dump(results, fp, indent=4)
    
    logger.info(f"SimulationID: {simulation_id} saved successfully to {results_fp}.")


@cli.command()
def get(results_fp: str, key: str):
    authed = check_key(key)
    if authed:
        with open(results_fp, "w") as fp:
            return json.load(fp)
    else:
        raise AuthenticationError(f"Key {key} could not be authorized.")
    

