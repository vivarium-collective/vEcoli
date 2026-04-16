"""
Generate a Nextflow workflow by interpreting a process-bigraph composite
document.

This runscript is a parallel, composite-document-driven alternative to
``runscripts/workflow.py`` (which assembles Nextflow text from string
fragments). The two scripts coexist — nothing here touches ``workflow.py``.

The composite describes the same pipeline shape — ParCa, createVariants,
simulation, analyses — as a network of Steps wired through shared state
paths. A plumbing ``Combine`` materialises the variant × seed product,
and simulation runs are scattered across it. The Step classes carry
``nextflow_script`` bodies and class-level ``nextflow_directives`` so the
renderer can emit realistic ``process { }`` blocks without consulting
any external template.

See ``doc/nextflow_composite_spec.md`` for the design.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path as FSPath

from bigraph_schema import allocate_core
from process_bigraph import Step, Composite


# ---------------------------------------------------------------------------
# Step classes for the workflow DAG
# ---------------------------------------------------------------------------


class ParcaStep(Step):
    """Run ParCa once; emit sim_data + config artefacts.

    Outputs one record carrying the config URI, sim_data URI, and their
    content hashes — the same shape the existing workflow.py emits as
    the ``parca_out`` channel.
    """

    nextflow_directives = {'label': 'parca'}

    def inputs(self):
        return {'config': 'string'}

    def outputs(self):
        return {'parca_out': 'list'}

    def update(self, state):
        return {'parca_out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            'export config_hash=$(sha256sum $config | cut -d\' \' -f1)\n'
            'PYTHONUNBUFFERED=1 python ${params.projectRoot}/runscripts/parca.py \\\n'
            '    --config "$config" -o "${params.publishDir}/${params.experimentId}/parca" --cpus ${task.cpus}\n'
            'export kb_hash=$(cat kb_hash.txt)\n'
            '"""'
        )


class CreateVariantsStep(Step):
    """Expand the config into a list of variant records."""

    nextflow_directives = {'label': 'variants'}

    def inputs(self):
        return {'parca_out': 'list'}

    def outputs(self):
        return {'variants': 'list'}

    def update(self, state):
        return {'variants': []}

    def nextflow_script(self):
        return (
            '"""\n'
            'PYTHONUNBUFFERED=1 python ${params.projectRoot}/runscripts/create_variants.py \\\n'
            '    --config "${params.config}"\n'
            '"""'
        )


class SeedChannelStep(Step):
    """Emit the list of simulation seeds from config."""

    nextflow_directives = {'executor': 'local'}

    def inputs(self):
        return {}

    def outputs(self):
        return {'seeds': 'list[integer]'}

    def update(self, state):
        return {'seeds': []}

    def nextflow_script(self):
        return (
            '"""\n'
            'PYTHONUNBUFFERED=1 python ${params.projectRoot}/runscripts/emit_seeds.py \\\n'
            '    --config "${params.config}"\n'
            '"""'
        )


class SimStep(Step):
    """Run one simulation per (variant, seed) pair.

    The ``variant_seed`` input is declared ``_cardinality: per_match``
    so the Nextflow renderer will treat its upstream channel as a queue
    channel and invoke this process once per item — i.e. scatter.
    """

    nextflow_directives = {
        'label': 'sim',
        'cpus': 2,
        'memory': '8 GB',
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
            'PYTHONUNBUFFERED=1 python ${params.projectRoot}/runscripts/sim.py \\\n'
            '    --config "${params.config}" \\\n'
            '    --sim-data-path "${parca_out[2]}" \\\n'
            '    --variant "${variant_seed[0]}" \\\n'
            '    --seed "${variant_seed[1]}"\n'
            '"""'
        )


class AnalysisMultiSeedStep(Step):
    """Per-variant analysis gathered across seeds."""

    nextflow_directives = {'label': 'analysis'}

    def inputs(self):
        return {'by_variant': 'map[list]'}

    def outputs(self):
        return {'multiseed_out': 'list'}

    def update(self, state):
        return {'multiseed_out': []}

    def nextflow_script(self):
        return (
            '"""\n'
            'PYTHONUNBUFFERED=1 python ${params.projectRoot}/runscripts/analysis.py \\\n'
            '    --kind multiseed \\\n'
            '    --config "${params.config}"\n'
            '"""'
        )


# ---------------------------------------------------------------------------
# Composite construction
# ---------------------------------------------------------------------------


def build_workflow_composite(core) -> Composite:
    core.register_link('ParcaStep', ParcaStep)
    core.register_link('CreateVariantsStep', CreateVariantsStep)
    core.register_link('SeedChannelStep', SeedChannelStep)
    core.register_link('SimStep', SimStep)
    core.register_link('AnalysisMultiSeedStep', AnalysisMultiSeedStep)

    schema = {
        'config_path': 'string',
        'parca_out': 'list',
        'variants': 'list',
        'seeds': 'list[integer]',
        'variant_seed_pairs': 'list',
        'sim_outputs': 'list',
        'sim_by_variant': 'map[list]',
        'multiseed_out': 'list',
    }

    state = {
        'config_path': '',
        'parca_out': [],
        'variants': [],
        'seeds': [],
        'variant_seed_pairs': [],
        'sim_outputs': [],
        'sim_by_variant': {},
        'multiseed_out': [],

        'parca': {
            '_type': 'step', 'address': 'local:ParcaStep',
            'inputs': {'config': ['config_path']},
            'outputs': {'parca_out': ['parca_out']},
        },
        'variants_step': {
            '_type': 'step', 'address': 'local:CreateVariantsStep',
            'inputs': {'parca_out': ['parca_out']},
            'outputs': {'variants': ['variants']},
        },
        'seeds_step': {
            '_type': 'step', 'address': 'local:SeedChannelStep',
            'inputs': {},
            'outputs': {'seeds': ['seeds']},
        },
        'variant_seed_combine': {
            '_type': 'step', 'address': 'local:Combine',
            'inputs': {'a': ['variants'], 'b': ['seeds']},
            'outputs': {'product': ['variant_seed_pairs']},
        },
        'sim': {
            '_type': 'step', 'address': 'local:SimStep',
            'inputs': {
                'parca_out': ['parca_out'],
                'variant_seed': ['variant_seed_pairs'],
            },
            'outputs': {'sim_out': ['sim_outputs']},
        },
        'group_by_variant': {
            '_type': 'step', 'address': 'local:GroupBy',
            'config': {'key_field': 'variant'},
            'inputs': {'stream': ['sim_outputs']},
            'outputs': {'groups': ['sim_by_variant']},
        },
        'analysis_multiseed': {
            '_type': 'step', 'address': 'local:AnalysisMultiSeedStep',
            'inputs': {'by_variant': ['sim_by_variant']},
            'outputs': {'multiseed_out': ['multiseed_out']},
        },
    }

    bridge = {
        'inputs': {
            'config': ['config_path'],
        },
        'outputs': {
            'multiseed': ['multiseed_out'],
        },
    }

    return Composite(
        {'schema': schema, 'state': state, 'bridge': bridge},
        core=core)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '-o', '--output',
        default='out/composite_workflow.nf',
        help='Path to write the rendered Nextflow document (default: %(default)s)',
    )
    parser.add_argument(
        '--workflow-name',
        default='main',
        help='Entry workflow name (default: %(default)s)',
    )
    args = parser.parse_args()

    core = allocate_core()
    composite = build_workflow_composite(core)
    rendered = composite.nextflow({'workflow_name': args.workflow_name})

    output = FSPath(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)

    print(f'wrote {output} ({len(rendered)} bytes, '
          f'{len(composite.step_paths)} steps)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
