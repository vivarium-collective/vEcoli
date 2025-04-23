"""
Base vivarium instance with parquet emitter config.

emitter config is as such:

config_schema = {
        'batch_size': {
            '_type': 'integer',
            '_default': 400
        },
        # ONE of the following:
        'out_dir': 'maybe[string]',  # local output directory (absolute/relative),
        'out_uri': 'maybe[string]'  # Google Cloud storage bucket URI
    }

TODO: replace locally encrypted out_dir with secure URI    
"""


import os
from ecoli.shared.base import vivarium_factory, EmitterConfig
from ecoli.shared.registration import ecoli_core


DEFAULT_EMITTER_ADDRESS = "local:parquet-emitter"
DATA_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(
            os.path.dirname(__file__)
        )
    ),
    "storage",
    "data"
)

emitter_config = EmitterConfig(
    address=DEFAULT_EMITTER_ADDRESS,
    config={
        "out_dir": DATA_DIR
    }
)
vivarium_base = vivarium_factory(core=ecoli_core, emitter_config=emitter_config)