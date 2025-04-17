"""
TODO: track down and re-implement the evolve method (unpickle, set, run, pickle) for i in duration
"""


from fastapi import APIRouter, Depends, UploadFile, File, Body, Query
from vivarium import Vivarium

from api.data_model.vivarium import VivariumDocument, VivariumMetadata
from api.gateway.auth import get_evolve_user
from api.handlers.encryption.db import write
from api.handlers.vivarium import VivariumFactory, new_id


router = APIRouter()


@router.get("/", tags=["EvolveAPI"])
async def get_testroute(user: dict = Depends(get_evolve_user)):
    return user


@router.post('/add/core', operation_id='add-core', tags=["EvolveAPI"])
async def add_core(
    core_spec: UploadFile = File(..., description="new pbg.ProcessTypes instance with registered types and processes")):
    pass 


@router.post(
    '/create', 
    operation_id='create', 
    tags=["EvolveAPI"]
)
async def create_vivarium(
    private_key: str = Query(default="1"),  # make_test_password("example")
    document: VivariumDocument | None = Body(default=None),
    name: str = Query(default="new_example"),
    secure: bool = Query(default=True, description="This argument is not yet used, but will be whether or not to use the community API (secure)"),  # TODO: implement this
    protocol: str = Query(default="vivarium", description="This argument is not yet used, but will be to determine which core to use.")  # TODO: implement this
) -> VivariumMetadata:  
    new_vivarium_factory = VivariumFactory()

    v: Vivarium = new_vivarium_factory(document=document)
    viv_id = new_id(name)
    write(v, viv_id, private_key.encode('utf-8'))
    
    return VivariumMetadata(viv_id)
