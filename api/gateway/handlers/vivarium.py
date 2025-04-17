from typing import Any
import uuid
import shutil
import pickle
import os
import dataclasses as dc
import tempfile as tf

from vivarium.vivarium import Vivarium

from api.shared.managers.pickle import Pickler
from api.shared.registries import manager
from api.data_model.vivarium import VivariumDocument, VivariumMetadata


class VivariumFactory:
    def get_core(self, protocol: str = "base"):
        # TODO: parse protocol here and return different cores if needed 
        return None
    
    def new(self, document: VivariumDocument | None = None, core=None) -> Vivarium:
        c = self.get_core() or core
        return Vivarium(
            core=c, 
            processes=c.process_registry.registry, 
            types=c.types(), 
            document=document.to_dict() if document is not None else None
        )
    
    def __call__(self, document: VivariumDocument | None = None, core=None) -> Vivarium:
        return self.new(document, core)


# -- /new-vivarium logic -- #

def pickle_vivarium(v: Vivarium, viv_id: str):
    # pickler for IO
    pickler = Pickler()

    # pickle viv
    pickled_viv: bytes = pickle.dumps(v)

    # encrypt pickled_viv
    # encrypted_viv = encrypt_viv(pickled_viv)
    
    # create temp dir
    temp_dir = tf.mkdtemp()

    # make tmp_pickle_path:-> (temp_dir / viv_id).pckl
    tmp_pickle_path = os.path.join(temp_dir, f"{viv_id}.pckl")
    
    # write pickle to tmp_pickle_path
    with open(tmp_pickle_path, "wb") as f:
        f.write(pickled_viv)

    # del pickled_viv
    del pickled_viv
    
    # make pickle_location:-> upload/secure store the pickle file (TODO: do this in a bucket)
    pickler.write(tmp_pickle_path, viv_id)
    
    # remove tmp dir:-> shutil.rmtree(tmp_dir)
    shutil.rmtree(temp_dir)


def new_vivarium(name: str, document: VivariumDocument | None = None) -> VivariumMetadata:
    # new factory for vivs
    factory = VivariumFactory()
    
    # make viv
    v: Vivarium = factory(document=document)
    
    # create new viv id
    viv_id = new_id(name)
    
    # write pickle to db
    pickle_vivarium(v, viv_id)
    
    return VivariumMetadata(viv_id)


# -- /get-vivarium logic -- #

def fetch_vivarium(vivarium_id: str) -> Vivarium:
    pickler = Pickler()

    # get pickle path using viv_id
    # pickle_location = lookup_pickle(vivarium_id)

    # make tmp dir
    # temp_dir = tf.mkdtemp()

    # get pickled_viv:-> fetch/download/write .pckl file into tmp_dir from pickle location

    # return json.loads(pickled_viv)
    return pickler.read(vivarium_id)


def run_vivarium(document: VivariumDocument, duration: float) -> list[dict[str, Any]]:
    factory = VivariumFactory()
    viv = factory(document=document)
    
    if "emitter" not in viv.get_state().keys():
        viv.add_emitter()
    
    viv.run(duration)

    return viv.get_results()  # type: ignore


def run_stateful_vivarium(vivarium_id: str, duration: float):
    viv = fetch_vivarium(vivarium_id)

    # add emitter if needed
    if "emitter" not in viv.get_state().keys():
        viv.add_emitter()

    # run 
    viv.run(duration)

    # write evolved pickle to db
    pickle_vivarium(viv, vivarium_id)

    # TODO: encrypt/store this instead viv.get_results() 
    return VivariumDocument(**viv.make_document())


# -- misc logic -- #

def lookup_pickle(vivarium_id: str):
    # devise a way to lookup the pickle path/location using the viv_id
    print(f"TODO: somehow lookup pickle location from vivarium_id: {vivarium_id}")
    return ""


def new_uuid():
    return str(uuid.uuid4())


def new_id(name: str):
    return name + new_uuid()


def new_vivarium_name():
    return f"new-vivarium-{new_uuid()}"
