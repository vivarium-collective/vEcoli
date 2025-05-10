# `ecoli.migrated.listeners`:

This subpackage defines a number of `Step` implementations which 
use a uniform design. Such a design has been expressed through the `ListenerBase` interface, which seeks to bridge the gap between "v1" and "v2" listener step implementations. Each step defined in this subpackage implements the `ListenerBase` interface whose implementation is shown in
the following example:


```python
from ecoli.shared.interface import ListenerBase

class MyListener(ListenerBase):
    name: str = ...
    topology: dict = ...
    defaults: dict = ...

    def initialize(self, config):
        """Define input and output port dicts whose values define
            defaults originating from v1 port_schema() calls.
        
        NOTE: these port defaults are defined as instance attributes in this
            initialize method.
        """
        self.input_ports = {
            "listeners": {
                "coords": listener_schema({"x": 11.11, "y"; 2.22})
            }
        }

        self.output_ports = {
            "velocity": {"_default": 0.1122}
        }

        # configure other construction logic as needed
        ...
    
    def update_condition...

    def update...

```