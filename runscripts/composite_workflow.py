"""
Build and render vEcoli's Nextflow workflow from a single, richly
parameterized composite-document builder.

The DAG is invariant:

    parca -> (variants + seeds) -> combine -> scatter(sim) -> {gathers} -> {analyses}

Config-driven variation happens through three registries plus per-scope
analysis inclusion. The builder stays a single function; the potential
structure of step networks is not constrained — this module's registries
are specific to vEcoli, but any other pipeline with a different shape
just defines its own builder against the same primitives.

This runscript is a parallel, composite-document-driven alternative to
``runscripts/workflow.py``. Neither replaces the other.

See ``doc/nextflow_composite_spec.md`` and ``doc/method_api_spec.md``
for the design.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path as FSPath
from typing import Any, Dict, List, Optional, Tuple, Type

from bigraph_schema import allocate_core
from process_bigraph import Step, Composite


# =============================================================================
# Step classes — one per atom of the DAG
# =============================================================================


# Nextflow-specific port declaration helpers. The _nextflow_decl escape
# hatch on a port schema lets a Step express a literal Nextflow declaration
# that the structural renderer can't yet derive — e.g. tuple+env captures
# that cross the boundary between shell-script side effects and Groovy
# channel values. These are flagged TODO: candidate for a structured
# representation (see doc/nextflow_composite_spec.md §4).

# Output declaration mirroring template.nf's runParca: emits a 4-tuple
# carrying the config URI, its hash (captured from the shell), the KB
# publish URI, and the KB hash (also captured from the shell).
_PARCA_OUT_DECL = (
    "tuple val(params.config), env('config_hash'), "
    "val(\"${params.publishDir}/${params.experimentId}/parca/kb\"), "
    "env('kb_hash'), emit: parca_out"
)

# createVariants emits two named channels — a tuple channel per variant
# and a metadata-URI channel. The renderer's single-output-port
# convention doesn't express the named-emit split; we fold both into one
# multi-line output block via _nextflow_decl for now.
_VARIANTS_OUT_DECL = (
    "tuple val(config_uri), val(config_hash), path('variant_info.txt'), "
    "emit: variantInfo\n    env 'metadata_uri', emit: variantMetadataUri"
)


class ParcaStep(Step):
    """Fresh ParCa run: compute sim_data, emit URIs + hashes.

    Mirrors template.nf runParca: hashes the staged config, invokes
    ``runscripts/parca.py`` to write ``simData.cPickle`` to the publish
    dir via fsspec, then exports the KB hash from ``kb_hash.txt``.
    """
    nextflow_directives = {'label': 'parca'}
    nextflow_port_decls = {
        'config':    'path config',
        'parca_out': _PARCA_OUT_DECL,
    }

    def inputs(self):
        return {'config': 'string'}

    def outputs(self):
        return {'parca_out': 'list'}

    def update(self, state):
        return {'parca_out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "export config_hash=\\$(sha256sum $config | cut -d' ' -f1)\n"
            "PYTHONUNBUFFERED=1 python \\${params.projectRoot}/runscripts/parca.py \\\\\n"
            '    --config "$config" \\\\\n'
            '    -o "\\${params.publishDir}/\\${params.experimentId}/parca" \\\\\n'
            '    --cpus \\${task.cpus}\n'
            "export kb_hash=\\$(cat kb_hash.txt)\n"
            '"""'
        )


class SimDataCachedStep(Step):
    """Short-circuit for a pre-computed ``sim_data_path``.

    Emits the same 4-tuple as ParcaStep but reads the cached KB paths
    from ``params`` instead of computing them. Runs locally (no SLURM
    submission overhead for a pure metadata emit).
    """
    nextflow_directives = {'executor': 'local'}
    nextflow_port_decls = {
        'config':    'path config',
        'parca_out': _PARCA_OUT_DECL,
    }

    def inputs(self):
        return {'config': 'string'}

    def outputs(self):
        return {'parca_out': 'list'}

    def update(self, state):
        return {'parca_out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "export config_hash=\\$(sha256sum $config | cut -d' ' -f1)\n"
            "# Point at the cached KB; the sha256 is computed against the\n"
            "# cached pickle so downstream processes can still cache-invalidate.\n"
            "export kb_hash=\\$(sha256sum \\${params.sim_data_path} | cut -d' ' -f1)\n"
            '"""'
        )


class CreateVariantsStep(Step):
    """Expand the config's ``variants`` section into per-variant sim_data.

    Mirrors template.nf createVariants: invokes
    ``runscripts/create_variants.py`` which reads the KB and writes per-
    variant pickles to the publish dir. Two named-emit outputs:
    ``variantInfo`` (tuple per variant) and ``variantMetadataUri``.
    """
    nextflow_directives = {'label': 'slurm_submit'}
    nextflow_port_decls = {
        'parca_out': 'tuple val(config_uri), val(config_hash), val(kb_uri), val(kb_hash)',
        'variants':  _VARIANTS_OUT_DECL,
    }

    def inputs(self):
        return {'parca_out': 'list'}

    def outputs(self):
        return {'variants': 'list'}

    def update(self, state):
        return {'variants': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "PYTHONUNBUFFERED=1 python \\${params.projectRoot}/runscripts/create_variants.py \\\\\n"
            '    --config "${config_uri}" \\\\\n'
            '    --kb "${kb_uri}" \\\\\n'
            '    -o "\\${params.publishDir}/\\${params.experimentId}/variant_sim_data"\n'
            "export metadata_uri=\\$(cat metadata_uri.txt)\n"
            '"""'
        )


class SeedSharedStep(Step):
    """Seeds shared across variants: emits ``[seed..seed+n_init_sims)``.

    Runs locally: one-line Groovy channel expression would be tighter,
    but keeping it as a process means the seed generator is a
    first-class node in the composite doc (addressable, swappable).
    """
    nextflow_directives = {'executor': 'local'}
    config_schema = {
        'seed': 'integer{0}',
        'n_init_sims': 'integer{1}',
    }

    def inputs(self):
        return {'variants': 'list'}

    def outputs(self):
        return {'seeds': 'list[integer]'}

    def update(self, state):
        base, n = self.config['seed'], self.config['n_init_sims']
        return {'seeds': list(range(base, base + n))}

    def nextflow_script(self):
        return (
            '"""\n'
            "# Emit a seed list; consumer .flatMap()s it into a seed channel.\n"
            'seq \\${params.seed} $((\\${params.seed} + \\${params.n_init_sims} - 1)) '
            '| paste -sd \',\' - | sed \'s/^/[/;s/$/]/\' > seeds.json\n'
            '"""'
        )


class SeedDisjointStep(Step):
    """Per-variant non-overlapping seed ranges.

    Downstream join on variant index pairs each variant with its own
    seed range. The emitted stream is a flat list of ``{variant, seed}``
    entries that the scatter reads as a single match set.
    """
    nextflow_directives = {'executor': 'local'}
    config_schema = {
        'seed': 'integer{0}',
        'n_init_sims': 'integer{1}',
    }

    def inputs(self):
        return {'variants': 'list'}

    def outputs(self):
        return {'seeds': 'list'}

    def update(self, state):
        return {'seeds': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "# Placeholder: emit per-variant offset seed ranges. Real\n"
            "# implementation reads variant_info.txt and writes a JSON\n"
            "# list of {variant, seed} records.\n"
            'echo "[]" > seeds.json\n'
            '"""'
        )


class SimStep(Step):
    """Run one simulation per (variant, seed) pair.

    ``variant_seed`` is declared ``_cardinality: per_match``; the
    renderer leaves scatter semantics implicit in Nextflow's queue-
    channel behavior (each item → one process invocation). Native
    execution loops in ``Step.invoke()``.
    """
    nextflow_directives = {
        'label': 'sim',
        'cpus': 2,
        'memory': '8 GB',
    }

    nextflow_port_decls = {
        'parca_out':    'tuple val(config_uri), val(config_hash), val(kb_uri), val(kb_hash)',
        'variant_seed': 'tuple val(variant_uri), val(variant_hash), val(variant_name), val(lineage_seed)',
        'sim_out': (
            "tuple val(config_uri), val(kb_uri), val(variant_name), "
            "val(lineage_seed), val(1), env('agent_id'), emit: sim_out"
        ),
    }

    def inputs(self):
        return {
            'parca_out': 'list',
            'variant_seed': {
                '_type': 'list',
                '_cardinality': 'per_match',
            },
        }

    def outputs(self):
        return {'sim_out': 'list'}

    def update(self, state):
        return {'sim_out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "PYTHONUNBUFFERED=1 python \\${params.projectRoot}/runscripts/sim.py \\\\\n"
            '    --config "${config_uri}" \\\\\n'
            '    --sim_data_path "${kb_uri}/simData.cPickle" \\\\\n'
            '    --variant_sim_data_path "${variant_uri}" \\\\\n'
            '    --variant "${variant_name}" \\\\\n'
            '    --seed "${lineage_seed}"\n'
            "export agent_id=\\$(cat agent_id.txt 2>/dev/null || echo 0)\n"
            '"""'
        )


# --- Analysis steps: one per scope --------------------------------------------


class _AnalysisBase(Step):
    """Analysis Step base.

    Subclasses override ``input_type`` (scope-driven) and
    ``analysis_kind`` (CLI flag ``-t <kind>`` passed to analysis.py).
    Keeping the declared input type close to the scope avoids
    schema-resolve conflicts at the gather-output wire path.
    """
    nextflow_directives = {'label': 'analysis'}
    nextflow_port_decls = {'out': "path 'plots/*'"}
    input_type: str = 'list'
    analysis_kind: str = 'single'

    def inputs(self):
        return {'data': type(self).input_type}

    def outputs(self):
        return {'out': 'list'}

    def update(self, state):
        return {'out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            "PYTHONUNBUFFERED=1 python \\${params.projectRoot}/runscripts/analysis.py \\\\\n"
            '    --config "\\${params.config}" \\\\\n'
            '    -o "\\$(pwd)/plots" \\\\\n'
            f'    -t {type(self).analysis_kind}\n'
            '"""'
        )


class AnalysisMultiVariantStep(_AnalysisBase):
    """Across all variants — experiment-wide gather."""
    input_type = 'list'
    analysis_kind = 'multivariant'


class AnalysisMultiSeedStep(_AnalysisBase):
    """Grouped by variant — cohort across seeds."""
    input_type = 'map'
    analysis_kind = 'multiseed'


class AnalysisMultiGenerationStep(_AnalysisBase):
    """Grouped by (variant, seed) — lineage across generations."""
    input_type = 'map'
    analysis_kind = 'multigeneration'


class AnalysisMultiDaughterStep(_AnalysisBase):
    """Grouped by (variant, seed, generation) — across daughter cells."""
    input_type = 'map'
    analysis_kind = 'multidaughter'


class AnalysisSingleStep(_AnalysisBase):
    """Per cell — no grouping."""
    input_type = 'list'
    analysis_kind = 'single'


class AnalysisParcaStep(_AnalysisBase):
    """On ParCa output only — mirrors template.nf analysisParca."""
    input_type = 'list'
    analysis_kind = 'parca'
    nextflow_directives = {
        'label': 'analysis',
        'publishDir': '{ "${params.publishDir}/${params.experimentId}/parca/analysis" }, mode: "copy"',
    }
    nextflow_port_decls = {
        'data': 'tuple val(config_uri), val(config_hash), val(kb_uri), val(kb_hash)',
        'out':  "path 'plots/*'",
    }

    def nextflow_script(self):
        return (
            '"""\n'
            "PYTHONUNBUFFERED=1 python \\${params.projectRoot}/runscripts/analysis.py \\\\\n"
            '    --config "${config_uri}" \\\\\n'
            '    --sim_data_path="${kb_uri}/simData.cPickle" \\\\\n'
            '    --validation_data_path="${kb_uri}/validationData.cPickle" \\\\\n'
            '    -o "\\$(pwd)/plots" \\\\\n'
            '    -t parca\n'
            '"""'
        )


# =============================================================================
# Registries
# =============================================================================


@dataclass(frozen=True)
class AnalysisScope:
    """One row of the analysis-scope registry.

    A scope is the combination of *how sim outputs are grouped* and *which
    Step runs on each group*. Scopes with no grouping key feed sim output
    (or ParCa output) directly into the analysis Step.
    """
    step: Type[Step]
    grouping_key: Optional[str]          # None → no GroupBy
    input_source: str = 'sim_outputs'    # 'sim_outputs' | 'parca_out'
    requires_single_daughters: bool = False
    requires_both_daughters: bool = False


ANALYSIS_SCOPES: Dict[str, AnalysisScope] = {
    'multivariant':    AnalysisScope(AnalysisMultiVariantStep,    grouping_key=None),
    'multiseed':       AnalysisScope(AnalysisMultiSeedStep,       grouping_key='variant'),
    'multigeneration': AnalysisScope(AnalysisMultiGenerationStep, grouping_key='seed'),
    'multidaughter':   AnalysisScope(AnalysisMultiDaughterStep,   grouping_key='generation',
                                     requires_both_daughters=True),
    'single':          AnalysisScope(AnalysisSingleStep,          grouping_key=None),
    'parca':           AnalysisScope(AnalysisParcaStep,           grouping_key=None,
                                     input_source='parca_out'),
}

PARCA_MODES: Dict[str, Type[Step]] = {
    'fresh':  ParcaStep,
    'cached': SimDataCachedStep,
}

SEED_STRATEGIES: Dict[str, Type[Step]] = {
    'shared':   SeedSharedStep,
    'disjoint': SeedDisjointStep,
}

# Keys under `analysis_options` that are *not* scope names (they configure
# runner resources). Extend if new non-scope keys appear.
ANALYSIS_OPTIONS_RESERVED = frozenset({
    'cpus', 'memory_gb', 'slurm_time_hrs', 'duckdb_threads',
})


# =============================================================================
# Builder
# =============================================================================


def _step_node(address: str,
               inputs: Dict[str, List],
               outputs: Dict[str, List],
               config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Produce a step dict for the state tree."""
    node: Dict[str, Any] = {
        '_type': 'step',
        'address': f'local:{address}',
        'inputs': inputs,
        'outputs': outputs,
    }
    if config:
        node['config'] = config
    return node


def _validate_analysis_options(
        analysis_options: Dict[str, Any],
        single_daughters: bool,
) -> List[str]:
    """Return the list of scope names to include.

    Raises on any unknown scope name; silently skips reserved keys (cpus,
    memory_gb, ...). Drops `multidaughter` when `single_daughters=True`
    — only one daughter simulated per generation so nothing to group.
    """
    active: List[str] = []
    for key, value in analysis_options.items():
        if key in ANALYSIS_OPTIONS_RESERVED:
            continue
        if key not in ANALYSIS_SCOPES:
            raise ValueError(
                f"unknown analysis scope {key!r} in analysis_options; "
                f"valid scopes: {sorted(ANALYSIS_SCOPES)}")
        if not value:
            continue
        scope = ANALYSIS_SCOPES[key]
        if scope.requires_both_daughters and single_daughters:
            # Nothing to group across when only one daughter runs per gen.
            continue
        active.append(key)
    return active


def build_ecoli_workflow(sim_config: Dict[str, Any]) -> Composite:
    """Build the vEcoli lineage workflow as a process-bigraph Composite.

    Args:
        sim_config: the full vEcoli sim config (as produced by
            ``load_config_with_inheritance``). Keys consumed:
            ``sim_data_path``, ``different_seeds_per_variant``,
            ``single_daughters``, ``n_init_sims``, ``seed``,
            ``analysis_options``.

    Returns:
        A realized ``Composite`` ready for ``.nextflow(options)``.
    """
    core = allocate_core()

    parca_mode = 'cached' if sim_config.get('sim_data_path') else 'fresh'
    seed_strategy = (
        'disjoint' if sim_config.get('different_seeds_per_variant') else 'shared')
    single_daughters = bool(sim_config.get('single_daughters', True))
    seed = int(sim_config.get('seed', 0))
    n_init_sims = int(sim_config.get('n_init_sims', 1))

    analysis_options = sim_config.get('analysis_options', {}) or {}
    active_scopes = _validate_analysis_options(analysis_options, single_daughters)

    # Register Step classes we'll reference by short name.
    registered_classes = {
        PARCA_MODES[parca_mode].__name__:     PARCA_MODES[parca_mode],
        SEED_STRATEGIES[seed_strategy].__name__: SEED_STRATEGIES[seed_strategy],
        'CreateVariantsStep': CreateVariantsStep,
        'SimStep':            SimStep,
    }
    for name in active_scopes:
        scope_step = ANALYSIS_SCOPES[name].step
        registered_classes[scope_step.__name__] = scope_step
    for short, cls in registered_classes.items():
        core.register_link(short, cls)

    # Build the invariant skeleton. Child keys and data-path keys live in
    # separate namespaces (children carry a ``_step_`` suffix) so a
    # child step's state entry doesn't accidentally shadow a data path.
    children: Dict[str, Dict[str, Any]] = {}
    children['step_parca'] = _step_node(
        PARCA_MODES[parca_mode].__name__,
        inputs={'config': ['config_path']},
        outputs={'parca_out': ['parca_out']})
    children['step_variants'] = _step_node(
        'CreateVariantsStep',
        inputs={'parca_out': ['parca_out']},
        outputs={'variants': ['variants']})
    children['step_seeds'] = _step_node(
        SEED_STRATEGIES[seed_strategy].__name__,
        inputs={'variants': ['variants']},
        outputs={'seeds': ['seeds']},
        config={'seed': seed, 'n_init_sims': n_init_sims})
    children['step_vs_combine'] = _step_node(
        'Combine',
        inputs={'a': ['variants'], 'b': ['seeds']},
        outputs={'product': ['variant_seed_pairs']})
    children['step_sim'] = _step_node(
        'SimStep',
        inputs={
            'parca_out': ['parca_out'],
            'variant_seed': ['variant_seed_pairs'],
        },
        outputs={'sim_out': ['sim_outputs']})

    # Build the schema skeleton for those invariant data paths.
    schema: Dict[str, Any] = {
        'config_path':          'string',
        'parca_out':            'list',
        'variants':             'list',
        'seeds':                'list',
        'variant_seed_pairs':   'list',
        'sim_outputs':          'list',
    }
    state: Dict[str, Any] = {
        'config_path':          '',
        'parca_out':            [],
        'variants':             [],
        'seeds':                [],
        'variant_seed_pairs':   [],
        'sim_outputs':          [],
    }

    # Inject analyses. Each scope optionally inserts a GroupBy + its
    # analysis Step, wired to sim_outputs or parca_out per the registry.
    # Scope data paths are distinct from the invariant paths above, so
    # the ``parca`` scope's output lands at ``analysis_parca_out`` and
    # doesn't collide with the upstream ``parca_out`` data path.
    for name in active_scopes:
        scope = ANALYSIS_SCOPES[name]
        upstream = [scope.input_source]

        if scope.grouping_key is not None:
            groups_path = f'{name}_groups'
            children[f'step_gather_{name}'] = _step_node(
                'GroupBy',
                inputs={'stream': upstream},
                outputs={'groups': [groups_path]},
                config={'key_field': scope.grouping_key})
            # Let the schema be inferred from the gather's output port
            # type; the downstream analysis step declares its input as
            # `map` so they resolve without conflict.
            state[groups_path] = {}
            upstream = [groups_path]

        out_path = f'analysis_{name}_out'
        children[f'step_analysis_{name}'] = _step_node(
            scope.step.__name__,
            inputs={'data': upstream},
            outputs={'out': [out_path]})
        schema[out_path] = 'list'
        state[out_path] = []

    # Merge children into state tree.
    state.update(children)

    bridge = {
        'inputs':  {'config':  ['config_path']},
        'outputs': {name: [f'analysis_{name}_out'] for name in active_scopes},
    }

    return Composite(
        {'schema': schema, 'state': state, 'bridge': bridge},
        core=core)


# =============================================================================
# CLI
# =============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-c', '--config',
        help='Path to a vEcoli JSON config (uses sensible defaults if omitted).')
    parser.add_argument(
        '-o', '--output',
        default='out/composite_workflow.nf',
        help='Where to write the rendered Nextflow document (default: %(default)s).')
    parser.add_argument(
        '--workflow-name', default='main',
        help='Entry workflow name (default: %(default)s).')
    args = parser.parse_args()

    if args.config:
        with open(args.config) as fh:
            sim_config = json.load(fh)
    else:
        sim_config = {
            'n_init_sims': 1,
            'seed': 0,
            'single_daughters': True,
            'analysis_options': {
                'multiseed': {'mass': {}},
                'single':    {'chromosome_animation': {}},
                'parca':     {'ribosome_counts': {}},
            },
        }

    composite = build_ecoli_workflow(sim_config)
    rendered = composite.nextflow({'workflow_name': args.workflow_name})

    output = FSPath(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)

    print(f'wrote {output} ({len(rendered)} bytes, '
          f'{len(composite.step_paths)} steps)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
