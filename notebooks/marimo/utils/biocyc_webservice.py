import json
from pathlib import Path

import requests
import os

from dotenv import load_dotenv


def biocyc_credentials(dir_credentials, s: requests.Session):
    s = requests.Session()
    cred_path = os.path.join(dir_credentials, "biocyc_credentials.json")
    with open(cred_path, "r") as f:
        credentials = json.load(f)
    s.post(
        "https://websvc.biocyc.org/credentials/login/",
        data={"email": credentials["email"], "password": credentials["password"]},
    )

    return s
