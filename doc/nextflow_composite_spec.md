# Nextflow Generation from Composite Documents — Annotation Spec

## Goal

Replace the string-fragment workflow generator in `runscripts/workflow.py` with a
method that interprets a process-bigraph composite document and emits a Nextflow
workflow. Each step/schema declares its own dataflow semantics; a top-level
orchestrator links them.

The spec below defines the three annotations needed to close the expressiveness
gap between process-bigraph and Nextflow, plus a fourth group of
Nextflow-specific annotations that carry execution semantics the orchestrator
consumes during rendering.

---

## 1. `_cardinality` — step execution cardinality over wildcard matches

**Problem.** Star paths today resolve "which states match this pattern," and the
matched set is collected at the step's port (gather). Nextflow's core primitive
is scatter: run the step once per match. Same wiring, different execution rule.

**Annotation.** On a Step's port declaration:

```python
inputs = {
    'variants': {
        '_path': ['variants', '*'],
        '_cardinality': 'per_match',   # scatter
    },
    'sim_data': {
        '_path': ['sim_data'],
        '_cardinality': 'one',         # default; single-instance
    },
}
```

**Semantics.**

| Value         | Meaning                                                                                 |
|---------------|-----------------------------------------------------------------------------------------|
| `one`         | Default. Step instantiated once; wildcard matches gathered into array/dict at the port. |
| `per_match`   | Step instantiated once per match; each instance sees one value at the port.             |

**Interaction rules.**
- **One scatter axis per Step.** A Step may declare `per_match` on at most one
  input group. `realize()` raises if more than one is found. Multiple axes
  require an explicit `Combine` Step upstream to materialize the product into
  a single match set. This keeps cardinality visible in the composite document
  and matches Nextflow's per-invocation tuple semantics.
- Outputs of a `per_match` Step are wildcard-shaped at the same axis as the
  input; downstream gather is implicit unless the consumer also declares
  `per_match`.
- **Static match sets only.** `realize()` expands `per_match` Steps into N
  instances at document-realization time from the statically-known match set.
  Runtime-dynamic scatter (e.g. fan-out over the rows of a file read at
  execution time) is not supported. vEcoli's workflows are all config-driven
  and realize-time-resolvable; if a future workflow needs runtime fan-out, a
  `per_match_runtime` variant with deferred expansion is the extension point.

**Maps to Nextflow.** A `per_match` input becomes a queue channel feeding the
process; `one` becomes a value channel (or a `.collect()`'d queue). This is one
of the inputs to the second-pass channel-kind deduction.

---

## 2. Plumbing step types — `Combine`, `GroupBy`, `Join`, `Mix`, `Collect`

**Problem.** `mix` and `collect` fall out of existing wiring (additive apply on
array ports, star-path gather). `combine` (Cartesian product), `groupTuple`, and
`join` need a key or product rule that has to live somewhere. Wire-level
metadata accretes into a mini-DSL. Explicit Steps stay discoverable and
testable.

**Built-in Step types.** Provided by the framework, not by workflows:

```python
Combine(inputs={'a': ..., 'b': ...}, outputs={'product': ...})
# Cartesian product of two match sets.

GroupBy(inputs={'stream': ..., 'key_field': 'variant'},
        outputs={'groups': ...})
# Partition a match set by the value of `key_field`; emit {key -> list}.

Join(inputs={'left': ..., 'right': ..., 'on': ['variant', 'seed']},
     outputs={'joined': ...})
# Inner-join two match sets on shared key fields.

Mix(inputs={'streams': [...]}, outputs={'merged': ...})
# Concatenate multiple match sets into one.  (Mostly redundant with additive
# array ports; provided for readability in workflow composites.)

Collect(inputs={'stream': ...}, outputs={'list': ...})
# Gather a wildcard match set into a single list.  (Redundant with
# `_cardinality: one` on a star path; provided for symmetry.)
```

**Why Steps, not wire annotations.**
- The grouping key / join key has a natural home (step config), not a wire.
- These appear explicitly in the composite document, so readers see the
  dataflow shape without following port metadata.
- The Nextflow renderer translates each plumbing Step into the corresponding
  channel operator (`.combine()`, `.groupTuple()`, `.join()`, `.mix()`,
  `.collect()`); no other step type needs Nextflow-operator knowledge.
- They are executable in the native runtime too, so composites round-trip
  identically between Nextflow execution and in-process execution.

**Native-runtime implementations.** Each plumbing Step ships with a trivial
`update()` so composites behave identically in both render modes:

| Step      | Native implementation                                      |
|-----------|------------------------------------------------------------|
| `Mix`     | `chain(*streams)`                                          |
| `Collect` | gather wildcard matches into a list                        |
| `Combine` | `itertools.product(a, b)`                                  |
| `GroupBy` | fold into `{key: [items]}` on `key_field`                  |
| `Join`    | dict-based inner join on the `on` key-tuple                |

Pure dataflow, no state, no side effects.

**Execution placement.** Plumbing Steps are I/O-restructuring, not compute.
The Nextflow renderer always emits `executor 'local'` on them regardless of the
enclosing scope's executor — submitting a dict join to SLURM queues
microsecond-work behind minutes of wait. Directive overrides on a specific
plumbing Step are allowed but must be explicit; there is no inheritance from
the workflow's default executor.

---

## 3. `_cache` — memoization policy by content hash of triggering inputs

**Problem.** `_triggers` identifies which input changes should cause a step to
re-run (intra-run scheduling). It says nothing about whether a prior result
could be reused (inter-run memoization). Nextflow's `-resume` is exactly this
second axis.

**Annotation.** On a Step:

```python
_triggers = ['sim_data', 'variant', 'seed']   # already exists
_cache = 'by_hash'                            # new
_schema_version = '2026.04.15'                # required when _cache = 'by_hash'
```

**Semantics.**

| Value       | Meaning                                                                                                                |
|-------------|------------------------------------------------------------------------------------------------------------------------|
| `none`      | Default. Always execute when triggered.                                                                                |
| `by_hash`   | Serialize the triggering inputs (via the existing JSON-document serialize path), hash, look up; skip and load on hit.  |

**Where the cache lives.** Keyed by `(step_type, schema_version, input_hash)`;
backed by the same JSON-document store used for snapshots. The hash uses the
schema-driven serialize path, so every type that already round-trips through
the document can be cached without additional work.

**Invalidation on code change.** `_schema_version` is mandatory when
`_cache = 'by_hash'`; `realize()` raises if it is missing. Bumping the version
makes prior cache entries unreachable (not deleted — just invisible under the
new key). Rejected alternatives: source-hashing is noisy and formatting-sensitive;
auto-derived schema-signature hashing catches interface changes but misses
semantic-only bug fixes. Manual versioning is boring and puts control with the
developer.

**Relation to `_triggers`.** `_triggers` is the *relevance set* — the inputs
whose changes matter. `_cache` is the *memoization policy* over that set.
The two annotations share their input list; `_cache` adds a hash+lookup step
before execution.

**Maps to Nextflow.** `by_hash` corresponds to Nextflow's default process cache
behavior; `none` emits `cache false` on the process.

---

## 4. Nextflow-specific annotations (consumed only by the renderer)

These carry execution-environment semantics that don't affect in-process
execution. They live on Step schemas and are ignored by the native runtime.

```python
_nextflow = {
    'channel': 'value' | 'queue' | 'auto',       # auto = deduce in pass 2
    'publish': {'path': 'results/', 'mode': 'copy'},
    'cache': True | False | 'lenient' | 'deep',  # overrides _cache mapping
    'error_strategy': 'retry' | 'ignore' | 'terminate',
    'max_retries': int,
}

_nextflow_directives = {
    'cpus': 4,
    'memory': '8 GB',
    'time': '2h',
    'executor': 'slurm',
    'queue': 'short',
    'container': 'ecoli:v2',
}
```

Most Steps specify only `_nextflow_directives`. The `channel` field defaults to
`auto` and is resolved in the second pass from `_cardinality` + consumer count.

---

## The `nextflow()` method

On a Composite:

```python
def nextflow(self, options: dict) -> str:
    """
    Render this composite as a Nextflow workflow document.

    options:
        output_dir:      where process work directories go
        executor:        default executor (per-step override via directives)
        template:        path to a template.nf to embed into
        profile:         cluster preset name (slurm, local, gcloud, ...)
    """
```

**Algorithm (two passes).**

1. **Contribution pass.** Walk the step graph topologically. Ask each child Step
   for its `nextflow_process()` contribution (process block) and its port
   metadata (cardinality, cache policy, directives).
2. **Linking pass.** For each wire edge, resolve channel kind from the producer's
   cardinality and the consumer count. Emit `workflow { }` body: channel
   declarations, `.set{}` bindings, process invocations, plumbing operator calls
   from `Combine`/`GroupBy`/`Join` steps.

The top-level `nextflow()` method is the *only* place that knows the Nextflow
document structure. Everything else is declarative on the Steps.

---

## Resolved design decisions

1. **Multi-axis scatter → explicit.** At most one `per_match` port per Step;
   `realize()` raises otherwise. Multiple scatter axes require an upstream
   `Combine`. Keeps cardinality visible in the document; matches Nextflow's
   flat-tuple semantics per process invocation. (§1)
2. **Runtime-dynamic match sets → not supported.** Static match sets only;
   `realize()` expands `per_match` at document time. No workflow in the repo
   needs runtime fan-out. Future extension point reserved as
   `per_match_runtime`. (§1)
3. **Plumbing Steps native runtime → ships with the library.** Trivial
   `update()` for each (`chain`, `itertools.product`, etc.). Composites behave
   identically in native and Nextflow modes by construction. (§2)
4. **Cache invalidation → mandatory `_schema_version`.** Required whenever
   `_cache = 'by_hash'`. Cache key is `(step_type, schema_version,
   input_hash)`. Source-hashing and auto schema-signature hashing both rejected
   as too noisy or too coarse. (§3)
5. **Directive inheritance → plumbing always local.** Renderer emits
   `executor 'local'` on plumbing Steps regardless of scope. Explicit
   per-Step overrides allowed; no implicit inheritance. (§2)

---

## Migration path for `runscripts/workflow.py`

1. Introduce the three annotations and plumbing Step types in process-bigraph.
2. Build `WorkflowStep` composites for ParCa, createVariants, simGenN,
   analyses.
3. Implement `Composite.nextflow()` as the two-pass renderer.
4. Switch `runscripts/workflow.py` to: build the composite, call `nextflow()`,
   drop in the template. Cluster/container setup stays where it is.
5. Delete `generate_lineage()` and its string-fragment helpers once parity is
   confirmed against an existing workflow.
