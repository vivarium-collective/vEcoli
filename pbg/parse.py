from typing import Any


SCHEMA_MAPPER = {
    "integer": int,
    "float": float,
    "string": str,
    "boolean": bool,
    "list": list,
    "tuple": tuple,
}


def parse_defaults(defaults: dict[str, float | Any]):
    """Translates vivarium.core.Process.defaults into bigraph-schema types to be consumed by pbg.Composite."""
    config_schema = {}
    for k, v in defaults.copy().items():
        if not isinstance(v, dict):
            _type = ""
            for schema_type, python_type in SCHEMA_MAPPER.items():
                if isinstance(v, python_type):
                    _type = schema_type
                else:
                    _type = "any"

            config_schema[k] = {
                "_type": _type,  # TODO: provide a more specific lookup
                "_default": v,
            }
        else:
            config_schema[k] = parse_defaults(v)

    return config_schema
