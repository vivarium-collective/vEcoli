"""Emit a Nextflow-shaped trace CSV + cost_meta.json sidecar from
the composite_lineage MP / Ray runners.

The Nextflow per-gen workflow uploads
``s3://<bucket>/<prefix>/<exp>/<exp>/nextflow/trace--<exp>--*.csv`` at
the end of each run; ``runscripts/v1_v2_report.py`` reads it for
workflow wall-clock and per-task billing. The composite_lineage
runners are NOT Nextflow-driven and don't naturally produce one of
these files, so the report has no source of timing data for them.

This module fills that gap: at workflow end the runner calls
:func:`emit_synthetic_trace` with per-seed timings + the deploy
topology, and we write:

  - ``trace--<exp>--<timestamp>.csv``: one row per lineage, with
    submit/start/complete/duration in epoch ms, ``cpu_model`` from
    ``/proc/cpuinfo``. ``v1_v2_report.workflow_stats`` understands
    this directly.
  - ``cost_meta--<exp>.json``: deploy topology
    (mp_single_node | ray_cluster + instance config + n_workers).
    ``v1_v2_report`` uses this to compute cost via
    ``cost.single_node_cost`` / ``cost.cluster_cost`` rather than
    nextflow's per-task billing (which would over-count for MP/Ray
    where many "tasks" share the same physical instance).
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
from typing import Optional


_TRACE_FIELDS = (
    'name', 'native_id', 'status', 'submit', 'start', 'complete',
    'duration', 'realtime', 'exit', '%cpu', '%mem', 'rss',
    'peak_rss', 'error_action', 'attempt', 'cpu_model', 'workdir',
)


def _read_cpu_model() -> str:
    """First ``model name:`` line from /proc/cpuinfo, or 'unknown'.

    On Graviton hosts /proc/cpuinfo doesn't expose ``model name`` —
    look for ``CPU implementer``/``CPU part`` instead and map those
    to a Neoverse string that ``cost.CPU_MODEL_TO_INSTANCE`` knows.
    """
    try:
        with open('/proc/cpuinfo') as f:
            text = f.read()
    except OSError:
        return 'unknown'
    m = re.search(r'^model name\s*:\s*(.+)$', text, re.M)
    if m:
        return m.group(1).strip()
    # ARM / Graviton path
    impl = re.search(r'^CPU implementer\s*:\s*(0x[0-9a-fA-F]+)', text, re.M)
    part = re.search(r'^CPU part\s*:\s*(0x[0-9a-fA-F]+)', text, re.M)
    if impl and part and impl.group(1).lower() == '0x41':
        # Arm Ltd. Common Neoverse part numbers we care about:
        # 0xd40 = Neoverse-V1 (Graviton3, c7g)
        # 0xd0c = Neoverse-N1 (Graviton2, c6g)
        return {
            '0xd40': 'Neoverse-V1',
            '0xd0c': 'Neoverse-N1',
        }.get(part.group(1).lower(), f'arm-part-{part.group(1)}')
    return 'unknown'


def emit_synthetic_trace(
    out_uri: str,
    exp_id: str,
    workflow_t_start: float,
    workflow_t_end: float,
    per_seed: list[tuple[int, float, float]],
    deploy_meta: dict,
) -> Optional[str]:
    """Write a Nextflow-shaped trace CSV + cost_meta.json sidecar.

    Args:
        out_uri: parquet output base, e.g. ``s3://bucket/prefix/exp_id``.
            The trace lands at
            ``<out_uri>/<exp_id>/nextflow/trace--<exp_id>--<ts>.csv``
            so ``fetch_and_compare.sh``'s existing include filter
            picks it up.
        exp_id: experiment id (matches the parquet partition root).
        workflow_t_start: time.time() when the parent process began
            scheduling lineages.
        workflow_t_end: time.time() when all lineages completed.
        per_seed: list of ``(seed, t_seed_start, t_seed_end)`` tuples
            (each in seconds since epoch). One row per lineage.
        deploy_meta: dict with deploy topology, e.g.
            ``{"deploy_mode": "mp_single_node",
              "instance": "c7g.metal", "n_instances": 1}``
            or ``{"deploy_mode": "ray_cluster",
                  "head_instance": "t4g.large",
                  "worker_instance": "c7g.metal", "n_workers": 4}``.

    Returns the full URI of the written trace CSV (or None if the
    write fails for any reason — failure is non-fatal: the workflow
    has already succeeded, we're just decorating it).
    """
    cpu_model = _read_cpu_model()
    rows = []
    for seed, t_seed_start, t_seed_end in per_seed:
        sub_ms = int(t_seed_start * 1000)
        comp_ms = int(t_seed_end * 1000)
        rows.append({
            'name': f'sim_seed={seed}_lineage',
            'native_id': str(os.getpid()),
            'status': 'COMPLETED',
            'submit': sub_ms,
            'start': sub_ms,
            'complete': comp_ms,
            'duration': comp_ms - sub_ms,
            'realtime': comp_ms - sub_ms,
            'exit': '0',
            '%cpu': '-', '%mem': '-', 'rss': '-', 'peak_rss': '-',
            'error_action': '-', 'attempt': '1',
            'cpu_model': cpu_model,
            'workdir': '-',
        })

    timestamp = time.strftime('%Y-%m-%d--%H-%M-%S')
    trace_name = f'trace--{exp_id}--{timestamp}.csv'
    cost_name = f'cost_meta--{exp_id}.json'
    base_dir = f'{out_uri.rstrip("/")}/{exp_id}/nextflow'

    try:
        import fsspec
        fs, _ = fsspec.core.url_to_fs(base_dir)
        # makedirs on S3 is a no-op for s3fs (and silent if it fails);
        # local fsspec creates intermediate dirs.
        try:
            fs.makedirs(base_dir, exist_ok=True)
        except Exception:
            pass

        # 1. Trace CSV
        trace_uri = f'{base_dir}/{trace_name}'
        with fsspec.open(trace_uri, 'wt') as f:
            w = csv.DictWriter(f, fieldnames=list(_TRACE_FIELDS))
            w.writeheader()
            for r in rows:
                w.writerow(r)

        # 2. cost_meta.json — written every run (not timestamped) so
        # the latest one always wins; report is one-off.
        cost_uri = f'{base_dir}/{cost_name}'
        meta = {
            **deploy_meta,
            'cpu_model': cpu_model,
            'workflow_t_start': workflow_t_start,
            'workflow_t_end': workflow_t_end,
            'workflow_wall_s': workflow_t_end - workflow_t_start,
            'exp_id': exp_id,
        }
        with fsspec.open(cost_uri, 'wt') as f:
            json.dump(meta, f, indent=2)

        print(f'[trace] wrote {trace_uri}')
        print(f'[trace] wrote {cost_uri}')
        return trace_uri
    except Exception as e:
        print(f'[trace] WARNING: failed to write synthetic trace: {e}')
        return None
