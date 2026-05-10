"""Cost estimation helpers for vEcoli workflow comparisons.

GovCloud (us-gov-west-1) hourly USD rates, as of 2026-05. These are
**approximate** — Spot rates vary minute-to-minute, On-Demand rates
shift on AWS price reductions, and GovCloud rates run ~25% above
commercial. Update by re-running:

    aws --profile stanford-sso --region us-gov-west-1 \\
      ec2 describe-spot-price-history \\
      --instance-types c7g.metal --max-items 1

The numbers exist primarily so the v1/v2 cost ratio is meaningful;
absolute dollars are second-order.
"""
from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Rate tables (US-Gov-West-1, May 2026)
# ---------------------------------------------------------------------------
# Spot rates: typical 60-70% discount off On-Demand. Verified via
# describe-spot-price-history on a quiet weekday afternoon.
SPOT_USD_PER_HR = {
    'c7g.metal':    1.10,   # 64 vCPU Graviton3
    'c7g.16xlarge': 1.10,
    'c7g.8xlarge':  0.55,
    'c7g.4xlarge':  0.27,
    'c7g.2xlarge':  0.13,
    'c7g.xlarge':   0.07,
    'c6g.metal':    0.78,   # 64 vCPU Graviton2
    'c6g.16xlarge': 0.78,
    't4g.large':    0.025,  # 2 vCPU burst — head node sized
}

ON_DEMAND_USD_PER_HR = {
    'c7g.metal':    3.06,
    'c7g.16xlarge': 3.06,
    'c7g.8xlarge':  1.53,
    'c7g.4xlarge':  0.77,
    'c7g.2xlarge':  0.38,
    'c7g.xlarge':   0.19,
    'c6g.metal':    2.18,
    'c6g.16xlarge': 2.18,
    't4g.large':    0.084,
}

# CPU model strings (as recorded in Nextflow trace CSV) → instance
# type. The Graviton family has the same Neoverse cores across c7g
# sizes, so the model string only narrows to *family*; we default
# to c7g.metal because that's what `vecoli-arm` provisions for
# our per-gen sims (~32-64 vCPU per task).
CPU_MODEL_TO_INSTANCE = {
    'Neoverse-V1':           'c7g.metal',   # Graviton3
    'Neoverse-N1':           'c6g.metal',   # Graviton2
    'AWS Graviton3 Processor': 'c7g.metal',
    'AWS Graviton2 Processor': 'c6g.metal',
}


def lookup_rate(instance_or_model: str, *, spot: bool = True) -> Optional[float]:
    """Hourly $ rate for an instance type or CPU model. Returns
    ``None`` if unknown (e.g. a local-dev CPU)."""
    if not instance_or_model:
        return None
    table = SPOT_USD_PER_HR if spot else ON_DEMAND_USD_PER_HR
    if instance_or_model in table:
        return table[instance_or_model]
    inst = CPU_MODEL_TO_INSTANCE.get(instance_or_model)
    if inst is not None:
        return table.get(inst)
    return None


# ---------------------------------------------------------------------------
# Per-engine cost
# ---------------------------------------------------------------------------
def nextflow_cost(trace, head_wall_s: float, *,
                  head_instance: str = 't4g.large',
                  head_spot: bool = False,
                  task_spot: bool = True) -> tuple[float, str]:
    """Sum over Nextflow tasks: ``duration × rate(cpu_model)`` plus the
    head node On-Demand cost for the wall-clock duration.

    Returns ``(usd, human_readable_breakdown)``.
    """
    if trace is None:
        return 0.0, '-'
    head_rate = lookup_rate(head_instance, spot=head_spot) or 0.0
    head_cost = head_wall_s / 3600 * head_rate
    task_cost = 0.0
    skipped = 0
    n_tasks = 0
    for r in trace.iter_rows(named=True):
        if not r.get('name', '').startswith('sim_'):
            # Only count sim tasks (parca/analysis tasks run on the
            # head node and are subsumed by head_cost).
            continue
        n_tasks += 1
        rate = lookup_rate(r.get('cpu_model') or '', spot=task_spot)
        dur_ms = r.get('duration') or 0
        if rate is None:
            skipped += 1
            continue
        task_cost += dur_ms / 3.6e6 * rate
    total = head_cost + task_cost
    pricing = 'spot' if task_spot else 'OD'
    suffix = f' ({skipped} unknown CPU)' if skipped else ''
    breakdown = (
        f'head {head_instance} OD ${head_cost:.2f} + '
        f'{n_tasks} sim tasks {pricing} ${task_cost:.2f}{suffix}')
    return total, breakdown


def single_node_cost(wall_s: float, instance: str = 'c7g.metal',
                     on_demand: bool = True) -> tuple[float, str]:
    """``wall × rate(instance)``. For mp_single_node deploys."""
    rate = lookup_rate(instance, spot=not on_demand) or 0.0
    cost = wall_s / 3600 * rate
    pricing = 'OD' if on_demand else 'spot'
    return cost, f'{instance} {pricing} {wall_s/3600:.2f}h × ${rate:.3f}/hr'


def cluster_cost(wall_s: float, head_instance: str = 't4g.large',
                 worker_instance: str = 'c7g.metal',
                 n_workers: int = 1, head_on_demand: bool = True,
                 worker_on_demand: bool = True) -> tuple[float, str]:
    """Head + workers, both running for the full workflow wall.
    For ray_cluster deploys."""
    head_rate = lookup_rate(head_instance, spot=not head_on_demand) or 0.0
    worker_rate = lookup_rate(worker_instance,
                              spot=not worker_on_demand) or 0.0
    hours = wall_s / 3600
    head_cost = hours * head_rate
    worker_cost = hours * n_workers * worker_rate
    total = head_cost + worker_cost
    head_pricing = 'OD' if head_on_demand else 'spot'
    worker_pricing = 'OD' if worker_on_demand else 'spot'
    breakdown = (
        f'head {head_instance} {head_pricing} ${head_cost:.2f} + '
        f'{n_workers}× {worker_instance} {worker_pricing} '
        f'${worker_cost:.2f}')
    return total, breakdown
