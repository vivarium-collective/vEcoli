# Ecoli Configs

## Attributes:

- ### *`processes`*: Process implementations that DO NOT inherit from `Step`, `PartitionedProcess`, and processes whose execution order DOES NOT matter.

- ### *`flow`*: Processes which inherit from `Step`, `PartitionedProcess`, and processes whose execution order matters (which should be `Step`s).

*From the documentation*:
"For example, if we want to run the example process above after all other Steps have run in a timestep, we can add the following key-value pair to the flow: "death_threshold": [("ribosome_data_listener",)] because ribosome_data_listener is currently in the last execution layer in flow (see Partitioning)."