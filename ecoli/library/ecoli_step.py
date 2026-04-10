"""
===================
E. coli Step Bridge
===================

Bridge base classes that inherit from both vivarium-core and
process-bigraph, providing a unified interface for vEcoli processes.

Each vEcoli process defines:

- ``config_schema`` — typed configuration (replaces ``defaults``)
- ``inputs()`` — typed input port schema
- ``outputs()`` — typed output port schema
- ``update(state, interval)`` — primary computation method

The vivarium interface (``defaults``, ``ports_schema``,
``next_update``) is derived automatically for backward compatibility.

In v2 (composite engine), config is accessed via ``self.config``.
In v1 (vivarium engine), config is accessed via ``self.parameters``.
The bridge ensures both are available regardless of which path was used.
"""

from bigraph_schema.methods import default as schema_default
from vivarium.core.process import Step as VivariumStep, Process as VivariumProcess
from process_bigraph import Step as BigraphStep, Process as BigraphProcess


class EcoliStep(VivariumStep, BigraphStep):
    """Base class for vEcoli steps.

    Subclasses implement ``update(state, interval)`` as the primary
    computation.  ``next_update(timestep, states)`` is provided for
    vivarium Engine compatibility and delegates to ``update()``.
    """

    # Base config fields inherited by all vEcoli steps/processes.
    # Vivarium injects 'timestep' automatically; declaring it here
    # ensures it survives realize/fill.
    config_schema = {
        'timestep': 'float{1.0}',
    }
    _output_ports = None
    _input_only_ports = None

    @classmethod
    def _defaults_from_schema(cls):
        """Derive vivarium-style defaults dict from config_schema.

        Handles both dict specs ``{'_type': 'float', '_default': 0.5}``
        and inline specs ``'float{0.5}'``.
        """
        if not cls.config_schema:
            return {}
        result = {}
        for key, spec in cls.config_schema.items():
            if isinstance(spec, dict) and '_default' in spec:
                result[key] = spec['_default']
            elif isinstance(spec, str) and '{' in spec:
                type_str, _, default_str = spec.partition('{')
                default_str = default_str.rstrip('}')
                type_str = type_str.strip()
                if type_str == 'float':
                    result[key] = float(default_str)
                elif type_str == 'integer':
                    result[key] = int(default_str)
                elif type_str == 'boolean':
                    result[key] = default_str.lower() in ('true', '1', 'yes')
                elif type_str == 'string':
                    result[key] = default_str
                else:
                    result[key] = default_str
        return result

    def __init__(self, config=None, core=None):
        # realize_link calls edge_class(config, core).
        # vivarium calls Class(parameters_dict).
        # Both pass config as the first positional arg.

        if self.config_schema and not self.__class__.__dict__.get('defaults'):
            self.__class__.defaults = self._defaults_from_schema()

        # vivarium init — sets self.parameters (merges with defaults)
        VivariumStep.__init__(self, parameters=config)

        # process-bigraph init — sets self.config via Edge.__init__
        if core is not None:
            BigraphStep.__init__(self, config=config or {}, core=core)
        else:
            # Ensure self.config is available even without core.
            # Use self.parameters (post-merge with defaults) so
            # self.config and self.parameters are always equivalent.
            self._config = self.parameters

    def next_update(self, timestep, states):
        """vivarium Engine entry point — delegates to update()."""
        return self.update(states, timestep)

    def update(self, state, interval=None):
        """process-bigraph entry point — subclasses override this.

        If a subclass overrides ``next_update`` (vivarium-style) but not
        ``update``, delegate to the subclass's ``next_update`` so the
        composite engine path also picks up the logic. We skip the
        delegation when ``next_update`` is EcoliStep's own version (which
        just calls ``update``) to avoid infinite recursion.
        """
        cls = type(self)
        _delegation_bases = (EcoliStep, EcoliProcess)
        for klass in cls.__mro__:
            if 'next_update' in klass.__dict__:
                if klass not in _delegation_bases:
                    return klass.next_update(self, interval or 0, state)
                break
        return {}


class EcoliProcess(VivariumProcess, BigraphProcess):
    """Base class for vEcoli processes (time-driven, with interval).

    Same bridge pattern as EcoliStep but for temporal processes.
    """

    config_schema = {
        'timestep': 'float{1.0}',
    }
    _output_ports = None

    _defaults_from_schema = EcoliStep._defaults_from_schema

    def __init__(self, config=None, core=None):
        if self.config_schema and not self.__class__.__dict__.get('defaults'):
            self.__class__.defaults = self._defaults_from_schema()

        VivariumProcess.__init__(self, parameters=config)

        if core is not None:
            BigraphProcess.__init__(self, config=config or {}, core=core)
        else:
            self._config = self.parameters

    def next_update(self, timestep, states):
        return self.update(states, timestep)

    def update(self, state, interval):
        """Same delegation as EcoliStep.update."""
        cls = type(self)
        _delegation_bases = (EcoliStep, EcoliProcess)
        for klass in cls.__mro__:
            if 'next_update' in klass.__dict__:
                if klass not in _delegation_bases:
                    return klass.next_update(self, interval or 0, state)
                break
        return {}

    def calculate_timestep(self, interval_or_state, state=None):
        """Bridge both signatures:
        vivarium: calculate_timestep(states)
        process-bigraph: calculate_timestep(interval, state)
        """
        if state is None:
            return self.parameters.get('timestep', 1.0)
        return interval_or_state
