from typing import Callable, Any
import dataclasses as dc

import fastapi

from api.data_model.base import BaseClass


@dc.dataclass
class RouterConfig(BaseClass):
    router: fastapi.APIRouter
    prefix: str
    dependencies: list[Callable[[str], Any]] | None = None

    @property
    def id(self):
        if len(self.prefix) > 1:
            return self.prefix.split('/')[-1]
        