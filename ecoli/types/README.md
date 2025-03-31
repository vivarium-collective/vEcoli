## `ecoli/types`: Bigraph schema specs for project-specific types that are registered in __init__ at runtime
### TODO: execute this remotely prior to runtime and import the stored types for different protocols, i.e.; `http` or `hpc` or `secure`, etc

To register a custom type in bigraph schema/process bigraph, it must contain the following key/values:
```python
'_default', '_apply', '_check', '_serialize', '_deserialize', '_fold'
```

A type id must be registered along with its corresponding schema when using the `pbg.ProcessTypes()` core. **The parameters required to register a new type are fully defined in within the `.json` files of this directory**. _Each JSON file should only contain the schema spec for a **single type**.

Each new type file should be defined as an object with outermost keys representing the id you wish to register for the given schema child. For example, consider the following object:

```json
{
  "a": {
    "x": "float"
  }
}
```

This file will be consumed by the `core` and core will register the types, `a`, in which `x` is accessible.