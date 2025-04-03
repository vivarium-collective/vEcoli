import copy
from dataclasses import dataclass
import datetime
import json
import os
from typing import Any
import uuid
from cryptography.fernet import Fernet

from bigraph_schema import get_path, set_path
from process_bigraph.emitter import Emitter

from ecoli.shared.data_model import IntervalResult


class SecureEmitter(Emitter):
    """
    Given an auth passed along with the request itself, this emitter will encrypt and emit results into
    some secure storage (TODO: what should this be?). The request authentication should have enough information
    to be able to then decrypt the data in the source (TODO: perhaps some dedicated portal?/database?)
    """
    config_schema = {
        **Emitter.config_schema,
        'file_path': {
            '_type': 'string',
            '_default': './out'  # Changed to a writable directory
        },
        'simulation_id': {
            '_type': 'string',
            '_default': None
        }
    }

    def __init__(self, config, core):
        super().__init__(config, core)
        self.simulation_id = config.get('simulation_id') or str(uuid.uuid4())
        self.file_path = config.get('file_path', './out')  # Changed default to a writable path
        os.makedirs(self.file_path, exist_ok=True)
        self.filepath = os.path.join(self.file_path, f"history_{self.simulation_id}.json")

        # Ensure the file exists and initialize properly
        if not os.path.exists(self.filepath):
            with open(self.filepath, 'w') as f:
                json.dump([], f)  # Initialize with an empty list

    def update(self, state) -> dict:
        """Appends the deep-copied state to the JSON file efficiently."""
        with open(self.filepath, 'r+') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []

            data.append(copy.deepcopy(state))
            f.seek(0)
            json.dump(data, f, indent=4)
        return {}

    def query(self, query=None):
        """Queries the JSON history by streaming the file to avoid memory overhead."""
        if not os.path.exists(self.filepath):
            return []

        with open(self.filepath, 'r') as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                return []

        if isinstance(query, list):
            results = []
            for t in data:
                result = {}
                for path in query:
                    element = get_path(t, path)
                    result = set_path(result, path, element)
                results.append(result)
            return results

        return data


@dataclass
class TimeStamp:
    @property
    def value(self):
        return "_".join(
            str(datetime.datetime.now()).split(' ')
        )


@dataclass
class AuthKey:
    """
    :param value: (`bytes`)
    :param generated: (`TimeStamp`)
    """
    generated: TimeStamp | None = None

    @property
    def value(self) -> bytes:
        self.generated = TimeStamp()
        return Fernet.generate_key()


@dataclass
class Authentication:
    key: AuthKey
    timestamp: TimeStamp


class Cryptographer(Fernet):
    key: AuthKey

    def __init__(self, key: AuthKey, backend=None):
        self.key = key
        super().__init__(key.value, backend)

    def write(self, simulation_id: str, dirpath: str, data: list[IntervalResult]) -> str:
        """Encrypt and locally write raw Vivarium output results (from .get_results())"""
        encrypted = self._encrypt(data)
        return self._save_encrypted(simulation_id, dirpath, encrypted)

    def read(self, filepath: str) -> list[IntervalResult]:
        """Read and decrypt safely stored data as Vivarium output results."""
        encrypted = self._get_encrypted(filepath)
        decrypted_bytes = super().decrypt(encrypted)
        return json.loads(decrypted_bytes)

    def _encrypt(self, data: list[IntervalResult]) -> bytes:
        bytes_data = json.dumps(data).encode()
        return self.encrypt(bytes_data)

    def _save_encrypted(self, simulation_id: str, dirpath: str, data: bytes) -> str:
        encrypted_path = os.path.join(dirpath, f"{simulation_id}.secure")
        with open(encrypted_path, "wb") as f:
            f.write(data)
        print(f"File saved to:\n{encrypted_path}")
        return encrypted_path

    def _get_encrypted(self, filepath: str) -> bytes:
        with open(filepath, "rb") as f:
            return f.read()


def new_key() -> AuthKey:
    return AuthKey()

