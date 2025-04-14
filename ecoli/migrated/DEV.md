## _*Core processes to Migrate*_:
- [] __init__.py
- [X] allocator.py
- [X] bulk_timeline.py
- [X] cell_division.py
- [X] chemostat.py
- [X] chromosome_replication.py
- [X] chromosome_structure.py
- [X] complexation.py
- [X] concentrations_deriver.py
- [X] division_detector.py
- [] engine_process.py
- [X] enzyme_kinetics.py
- [X] equilibrium.py
- [X] global_clock.py
- [] metabolism.py
- [] metabolism_redux.py
- [] metabolism_redux_classic.py
- [X] partition.py
- [] polypeptide_elongation.py
- [] polypeptide_initiation.py
- [X] protein_degradation.py
- [X] registries.py
- [X] rna_degradation.py
- [X] rna_interference.py
- [X] rna_maturation.py
- [X] shape.py
- [X] tf_binding.py
- [X] tf_unbinding.py
- [X] transcript_elongation.py
- [X] transcript_initiation.py
- [X] two_component_system.py
- [X] unique_update.py

----------------------------------------------------------------------

## _*Auxiliary processes to Migrate:*_
### _`ecoli.processes.antibiotics`_:

### _`ecoli.processes.chemotaxis`_:

### _`ecoli.processes.environment`_:

### _`ecoli.processes.listeners`_:

### _`ecoli.processes.membrane`_:

### _`ecoli.processes.spatiality`_:

### _`ecoli.processes.stubs`_:


## _*Misc*_:
- [] for partitioned processes, parse exactly which inputs and which outputs should be defined, rather than bidirectional
- [] refactor bulk_state declarations to be numpy_schema("bulk")
- [] review numpy_schema and ensure the migrated version works
- [] Add formalized registration at module init of processes
- [] Add formalized registration at module init of types
- [] Clean up registration module
- [] Review partitioned process vs. process/step implementations
