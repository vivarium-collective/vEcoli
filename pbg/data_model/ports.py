from dataclasses import dataclass, asdict


@dataclass
class ProcessBigraphPorts:
    inputs: set
    outputs: set

    def serialize(self):
        return asdict(self)
