"""Endpoint definitions for the CommunityAPI. NOTE: Users of this API must be first authenticated."""


import dataclasses as dc
import datetime
from typing import Any 

from fastapi import APIRouter, Depends, UploadFile, File, Body, Query
import process_bigraph
from vivarium import Vivarium

from ecoli import ecoli_core

from api.data_model.base import BaseClass
from api.data_model.simulation import SimulationRun
from api.data_model.vivarium import VivariumDocument
from api.gateway.auth import get_user
from api.handlers.encryption.db import write
from api.handlers.multi import launch_scan
from api.handlers.vivarium import VivariumFactory, new_id


LOCAL_URL = "http://localhost:8080"
PROD_URL = ""  # TODO: define this
    

router = APIRouter()
viv_factory = VivariumFactory()

# e54d4431-5dab-474e-b71a-0db1fcb9e659

@router.get("/test-authentication", operation_id="test-authentication", tags=["CommunityAPI"])
async def test_authentication(user: dict = Depends(get_user)):
    return user


@router.post("/run", tags=["CommunityAPI"])
async def run_simulation(
    document: VivariumDocument,
    duration: float = Query(default=11.0),
    name: str = Query(default="community_simulation")
) -> SimulationRun:
    """TODO: instead, here emit a new RequestMessage to gRPC to server with document, duration, and sim_id and run
        it there, then storing the secured results in the server, and then return a sim result confirmation with sim_id
    """
    # make sim id
    sim_id = new_id(name)

    # emit payload message to websocket or grpc

    return SimulationRun(
        id=sim_id,  # ensure users can use this to retrieve the data later
        last_updated=str(datetime.datetime.now())
    )


@router.post(
    "/scan", 
    tags=["CommunityAPI"], 
    description="Launch n of the same/similar simulations in parallel async"
)
async def run_scan(
    document: VivariumDocument, 
    duration: float, 
    n_threads: int, 
    perturbation_config: dict[str, Any] | None = None,
    distribution_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    return launch_scan(document, duration, n_threads, perturbation_config, distribution_config)


# TODO: have the ecoli interval results call encryption.db.write for each interval
# TODO: have this method call encryption.db.read for interval data
@router.get(
    '/get/results', 
    operation_id='get-results', 
    tags=["CommunityAPI"]
)
async def get_results(key: str, simulation_id: str):
    # for now, data does not need to be encrypted as this api will only be 
    #  available if properly authenticated with an API Key.
    # viv = read(EncodedKey(key), vivarium_id)
    pass





# -- static data -- #

@router.get('/get/processes', tags=["CommunityAPI"])
def get_registered_processes() -> list[str]:
    # TODO: implement this for ecoli_core
    from ecoli import ecoli_core
    return list(ecoli_core.process_registry.registry.keys())


@router.get('/get/types', tags=["CommunityAPI"])
def get_registered_types() -> list[str]:
    # TODO: implement this for ecoli_core
    from ecoli import ecoli_core
    return list(ecoli_core.types().keys())