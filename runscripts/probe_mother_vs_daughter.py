"""Greenfield colony: full state + execution comparison of mother@init
vs daughter@post-divide.

Single run produces:
  - SNAPSHOT_MOTHER_INIT   — cell state right after Composite build
                              (run_steps_on_init=True priming done,
                              before any tick)
  - SNAPSHOT_MOTHER_TN     — cell state after N priming ticks
                              (so we know what a healthy "running"
                              cell looks like)
  - SNAPSHOT_DAUGHTER_POST — daughter '00' state right after
                              _divide sentinel applied, before any
                              post-divide step cascade
                              (``_halt_after_structural`` guarantees
                              this)
  - SNAPSHOT_DAUGHTER_TN   — daughter '00' state after N post-divide
                              ticks
  - TRACE_FILE             — every process invocation written by the
                              framework's PROCESS_BIGRAPH_TRACE_FILE
                              hook (path, sim_t, interval, input/
                              output summaries)

Then prints:
  A. Structural diff MOTHER_INIT vs DAUGHTER_POST
       — keys-only-in-mother, keys-only-in-daughter, value diffs
  B. Mass / FBA-critical field diff (cell_mass, dry_mass, volume,
     bulk.count.sum, unique counts)
  C. Process invocation order for mother's first 3 ticks vs daughter's
     first 3 post-divide ticks (ordered, same set?, any double-fires?)

Run:
  uv run python runscripts/probe_mother_vs_daughter.py \\
      --sim-data-path out/kb/simData.cPickle \\
      --trace-ticks 3 \\
      --trace-file /tmp/colony_trace.jsonl
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')

import argparse
import json
import sys
import time
from copy import deepcopy

import numpy as np

# Trace env var MUST be set before composite imports — module-level
# open() in process_bigraph.composite reads it once at import time.
ap_pre = argparse.ArgumentParser(add_help=False)
ap_pre.add_argument('--trace-file', default='/tmp/colony_trace.jsonl')
_pre_args, _ = ap_pre.parse_known_args()
os.environ['PROCESS_BIGRAPH_TRACE_FILE'] = _pre_args.trace_file
# Truncate the trace file at start so we don't append to a stale one.
open(_pre_args.trace_file, 'w').close()


from configs import CONFIG_DIR_PATH
from ecoli.library.bigraph_types import ECOLI_TYPES
from ecoli.composites.ecoli_composite import build_ecoli_document, run_to_division
from ecoli.experiments.ecoli_master_sim import EcoliSim
from ecoli.library.sim_data import LoadSimData
from process_bigraph import Composite, allocate_core


# -----------------------------------------------------------------------------
# State diff utilities
# -----------------------------------------------------------------------------

def _summarize(val, depth=0):
    """Compact, comparable summary of a cell field."""
    if depth > 4:
        return f'<deep:{type(val).__name__}>'
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, np.ndarray):
        out = {'_np': True, 'shape': list(val.shape), 'dtype': str(val.dtype)}
        try:
            if val.dtype.names:
                for f in val.dtype.names:
                    if val[f].dtype.kind in 'iuf':
                        out[f'sum.{f}'] = float(val[f].sum())
            elif val.dtype.kind in 'iuf':
                out['sum'] = float(val.sum())
        except Exception:
            pass
        return out
    if isinstance(val, dict):
        # Surface keys; recurse on each (small-dict summary)
        return {k: _summarize(v, depth + 1) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [_summarize(v, depth + 1) for v in val[:5]]
    return f'<{type(val).__name__}>'


def _walk(state, prefix=()):
    """Yield (path, leaf_summary) pairs for non-dict leaves."""
    if isinstance(state, dict):
        for k, v in state.items():
            if isinstance(v, dict):
                yield from _walk(v, prefix + (k,))
            else:
                yield prefix + (k,), _summarize(v)
    else:
        yield prefix, _summarize(state)


def diff_states(a_state, b_state, label_a='mother', label_b='daughter',
                value_diff_limit=80):
    a = dict(_walk(a_state))
    b = dict(_walk(b_state))
    keys_a = set(a)
    keys_b = set(b)
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = sorted(keys_a & keys_b)

    print(f'\n=== STATE DIFF: {label_a} vs {label_b} ===', flush=True)
    print(f'  only in {label_a}: {len(only_a)} paths', flush=True)
    for p in only_a[:30]:
        print(f'    - {".".join(str(x) for x in p)}', flush=True)
    print(f'  only in {label_b}: {len(only_b)} paths', flush=True)
    for p in only_b[:30]:
        print(f'    + {".".join(str(x) for x in p)}', flush=True)

    diffs = [(p, a[p], b[p]) for p in common if a[p] != b[p]]
    print(f'  value diffs: {len(diffs)} paths', flush=True)
    for p, va, vb in diffs[:value_diff_limit]:
        path = '.'.join(str(x) for x in p)
        print(f'    ~ {path}', flush=True)
        print(f'      {label_a}: {va}', flush=True)
        print(f'      {label_b}: {vb}', flush=True)


def snapshot_shape(composite):
    """Capture the engine-internal structure at a point in time so we
    can compare two snapshots later."""
    front = getattr(composite, 'front', {}) or {}
    return {
        'process_paths': sorted(composite.process_paths.keys()),
        'step_paths': sorted(composite.step_paths.keys()),
        'front_paths': sorted(front.keys()),
        'front_times': {p: front[p].get('time') for p in sorted(front.keys())},
        'to_run': list(getattr(composite, 'to_run', []) or []),
        'global_time': composite.state.get('global_time'),
    }


def print_shape(snap, label):
    pp = snap['process_paths']
    sp = snap['step_paths']
    print(f'\n--- {label} composite shape ---', flush=True)
    print(f'  global_time: {snap["global_time"]}', flush=True)
    print(f'  process_paths ({len(pp)}): '
          f'{[".".join(str(x) for x in p) for p in pp]}', flush=True)
    print(f'  step_paths ({len(sp)}):', flush=True)
    by_agent = {}
    for p in sp:
        if len(p) >= 2 and p[0] == 'agents':
            by_agent.setdefault(p[1], []).append(p[-1])
        else:
            by_agent.setdefault('<root>', []).append('.'.join(str(x) for x in p))
    for aid, names in sorted(by_agent.items()):
        print(f'    agent {aid!r} ({len(names)} steps): {names[:8]}'
              f'{"..." if len(names) > 8 else ""}', flush=True)
    front_paths = snap['front_paths']
    front_times = snap['front_times']
    print(f'  front (per-process next-update): {len(front_paths)} entries',
          flush=True)
    for p in front_paths[:6]:
        print(f'    {".".join(str(x) for x in p)}: '
              f'time={front_times[p]}', flush=True)
    print(f'  to_run (pending step queue): {len(snap["to_run"])} entries '
          f'{[".".join(str(x) for x in p) for p in snap["to_run"][:6]]}',
          flush=True)


def diff_shapes(snap_a, snap_b, label_a, label_b):
    """Compare two shape snapshots."""
    print(f'\n--- SHAPE DIFF: {label_a} vs {label_b} ---', flush=True)
    set_a_pp = set(tuple(p) for p in snap_a['process_paths'])
    set_b_pp = set(tuple(p) for p in snap_b['process_paths'])
    only_a = sorted(set_a_pp - set_b_pp)
    only_b = sorted(set_b_pp - set_a_pp)
    print(f'  process_paths: {len(set_a_pp)} vs {len(set_b_pp)}', flush=True)
    if only_a:
        print(f'    only in {label_a}: {only_a[:10]}', flush=True)
    if only_b:
        print(f'    only in {label_b}: {only_b[:10]}', flush=True)
    set_a_sp = set(tuple(p) for p in snap_a['step_paths'])
    set_b_sp = set(tuple(p) for p in snap_b['step_paths'])
    print(f'  step_paths: {len(set_a_sp)} vs {len(set_b_sp)}', flush=True)
    a_by_last = {p[-1] for p in set_a_sp}
    b_by_last = {p[-1] for p in set_b_sp}
    print(f'    {label_a} unique step types: {len(a_by_last)}', flush=True)
    print(f'    {label_b} unique step types: {len(b_by_last)}', flush=True)
    only_a_names = sorted(a_by_last - b_by_last)
    only_b_names = sorted(b_by_last - a_by_last)
    if only_a_names:
        print(f'    step names only in {label_a}: {only_a_names[:10]}',
              flush=True)
    if only_b_names:
        print(f'    step names only in {label_b}: {only_b_names[:10]}',
              flush=True)


def fba_critical(label, cell):
    """Print the fields most likely to make FBA infeasible if wrong."""
    print(f'\n--- {label} FBA-critical ---', flush=True)
    b = cell.get('bulk')
    if hasattr(b, 'dtype') and b.dtype.names and 'count' in b.dtype.names:
        print(f'  bulk.count: sum={int(b["count"].sum())} '
              f'shape={b["count"].shape} dtype={b["count"].dtype}',
              flush=True)
    listeners = cell.get('listeners') or {}
    if isinstance(listeners, dict):
        mass = listeners.get('mass') or {}
        if isinstance(mass, dict):
            for k in ('cell_mass', 'dry_mass', 'volume', 'protein_mass',
                      'rna_mass', 'water_mass'):
                v = mass.get(k)
                if isinstance(v, np.ndarray):
                    v = float(v.item()) if v.size == 1 else f'shape={v.shape}'
                print(f'  listeners.mass.{k} = {v}', flush=True)
    env = cell.get('environment') or {}
    if isinstance(env, dict):
        ext = env.get('exchange_data') or {}
        if isinstance(ext, dict):
            print(f'  environment.exchange_data keys: {sorted(ext.keys())[:8]}...',
                  flush=True)
    div = cell.get('division') or {}
    if isinstance(div, dict):
        cfg = div.get('config') or {}
        inst = div.get('instance')
        print(f'  division.config.agent_id = {cfg.get("agent_id")!r}', flush=True)
        if inst is not None:
            print(f'  division.instance.agent_id = '
                  f'{getattr(inst, "agent_id", None)!r}', flush=True)


# -----------------------------------------------------------------------------
# Trace analysis
# -----------------------------------------------------------------------------

def load_trace(path):
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def slice_trace(records, agent_id, t_start, t_end):
    """Records whose first path segment is 'agents/<agent_id>' and whose
    global_time is in [t_start, t_end)."""
    out = []
    for r in records:
        gt = r.get('gt')
        if gt is None or gt < t_start or gt >= t_end:
            continue
        path = r.get('path') or []
        if len(path) < 2 or path[0] != 'agents' or path[1] != agent_id:
            continue
        out.append(r)
    return out


def trace_summary(records, label):
    print(f'\n=== TRACE: {label} ===', flush=True)
    print(f'  total invocations: {len(records)}', flush=True)
    if not records:
        return
    by_tick = {}
    for r in records:
        gt = r.get('gt')
        by_tick.setdefault(gt, []).append(r)
    for gt in sorted(by_tick.keys())[:10]:
        seq = by_tick[gt]
        names = [r.get('path', [''])[-1] for r in seq]
        cls = [r.get('cls') for r in seq]
        print(f'  t={gt}: {len(seq)} invocations', flush=True)
        for i, (n, c) in enumerate(zip(names, cls)):
            print(f'    {i:3d}. {n}  ({c})', flush=True)


def diff_trace_order(mother_records, daughter_records,
                     mother_t0, daughter_t0):
    """Compare per-tick invocation sequences."""
    print(f'\n=== TRACE ORDER DIFF ===', flush=True)
    m_by_tick = {}
    for r in mother_records:
        m_by_tick.setdefault(r.get('gt'), []).append(
            (r.get('path', [''])[-1], r.get('cls')))
    d_by_tick = {}
    for r in daughter_records:
        d_by_tick.setdefault(r.get('gt'), []).append(
            (r.get('path', [''])[-1], r.get('cls')))

    m_ticks = sorted(m_by_tick.keys())
    d_ticks = sorted(d_by_tick.keys())
    n_compare = min(len(m_ticks), len(d_ticks))
    print(f'  comparing {n_compare} ticks each side', flush=True)
    for i in range(n_compare):
        mt, dt = m_ticks[i], d_ticks[i]
        mseq = m_by_tick[mt]
        dseq = d_by_tick[dt]
        if mseq == dseq:
            print(f'  tick m={mt} vs d={dt}: identical ({len(mseq)} fires)',
                  flush=True)
            continue
        print(f'  tick m={mt} vs d={dt}: DIFFER '
              f'(m={len(mseq)} fires, d={len(dseq)} fires)', flush=True)
        # Show first 10 of each side
        max_show = max(len(mseq), len(dseq), 10)
        for j in range(min(max_show, max(len(mseq), len(dseq)))):
            m_item = mseq[j] if j < len(mseq) else None
            d_item = dseq[j] if j < len(dseq) else None
            marker = ' ' if m_item == d_item else '*'
            print(f'    {marker} {j:3d}. m={m_item} | d={d_item}', flush=True)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--max-divide-duration', type=float, default=3000.0)
    ap.add_argument('--trace-ticks', type=int, default=3,
                    help='How many ticks to trace on each side.')
    ap.add_argument('--trace-file', default='/tmp/colony_trace.jsonl')
    args = ap.parse_args()

    # Sanity: trace path was already set before imports.
    assert os.environ.get('PROCESS_BIGRAPH_TRACE_FILE') == args.trace_file, (
        f'trace file env var not set; got '
        f'{os.environ.get("PROCESS_BIGRAPH_TRACE_FILE")!r}')

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = args.sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = 0
    sim_config['divide'] = True

    core = allocate_core()
    core.register_types(ECOLI_TYPES)
    lsd = LoadSimData(**{**sim_config, 'seed': 0})

    print('[probe] building gen-0 mother cell...', flush=True)
    t0 = time.perf_counter()
    state = build_ecoli_document(core, sim_config, load_sim_data=lsd)
    composite = Composite(
        {'schema': {}, 'state': state, 'run_steps_on_init': True},
        core=core)
    print(f'[probe]   built in {time.perf_counter()-t0:.1f}s', flush=True)

    # SNAPSHOT A: mother @ t=0 (post init-steps, pre tick)
    mother_init = deepcopy(composite.state['agents']['0'])
    shape_mother_init = snapshot_shape(composite)

    # Trace mother's first N ticks
    print(f'[probe] running mother {args.trace_ticks}s (traced)...', flush=True)
    composite.run(float(args.trace_ticks))
    mother_t_n = deepcopy(composite.state['agents']['0'])

    # Continue to division
    print(f'[probe] driving mother to division (max={args.max_divide_duration}s)...',
          flush=True)
    t0 = time.perf_counter()
    divided, ct = run_to_division(composite,
                                  max_duration=args.max_divide_duration)
    print(f'[probe]   divided={divided} t={ct:.1f} '
          f'wall={time.perf_counter()-t0:.1f}s', flush=True)
    if not divided:
        print('[probe] NO DIVIDE — bailing', flush=True)
        return

    divide_t = ct
    keys = sorted(composite.state['agents'].keys())
    print(f'[probe]   agents after divide: {keys}', flush=True)
    if len(keys) < 2:
        print('[probe] unexpected: <2 daughters after divide', flush=True)
        return

    daughter_id = keys[0]
    daughter_post = deepcopy(composite.state['agents'][daughter_id])
    shape_post_divide = snapshot_shape(composite)

    # Run a few post-divide ticks (traced)
    # IMPORTANT: must clear the halt flag or run() returns instantly when
    # the cached _last_apply_structural is still True.
    composite._halt_after_structural = False
    composite._last_apply_structural = False
    print(f'[probe] ticking daughters {args.trace_ticks}s (traced)...',
          flush=True)
    composite.run(float(args.trace_ticks))
    daughter_t_n = deepcopy(composite.state['agents'].get(daughter_id, {}))
    shape_after_daughter_ticks = snapshot_shape(composite)

    # ----- Reports -----
    print_shape(shape_mother_init, 'composite @ mother init')
    print_shape(shape_post_divide, 'composite @ post-divide')
    print_shape(shape_after_daughter_ticks,
                f'composite @ post-divide + {args.trace_ticks}s')
    diff_shapes(shape_mother_init, shape_post_divide,
                'mother@init', 'post-divide')

    fba_critical('mother @ t=0', mother_init)
    fba_critical(f'mother @ t={args.trace_ticks}', mother_t_n)
    fba_critical(f'daughter {daughter_id!r} @ post-divide '
                 f'(t={divide_t:.1f})', daughter_post)
    fba_critical(f'daughter {daughter_id!r} @ t=divide+{args.trace_ticks}s',
                 daughter_t_n)

    diff_states(mother_init, daughter_post,
                label_a='MOTHER@t=0', label_b=f'DAUGHTER@post-divide',
                value_diff_limit=60)

    # Trace analysis
    print(f'\n[probe] loading trace from {args.trace_file}...', flush=True)
    records = load_trace(args.trace_file)
    print(f'[probe]   {len(records)} trace records', flush=True)

    mother_first = slice_trace(records, '0', 0.0, float(args.trace_ticks) + 0.1)
    daughter_first = slice_trace(records, daughter_id, divide_t,
                                 divide_t + float(args.trace_ticks) + 0.1)

    trace_summary(mother_first[:80], 'MOTHER first ticks (head)')
    trace_summary(daughter_first[:80],
                  f'DAUGHTER {daughter_id!r} first post-divide ticks (head)')
    diff_trace_order(mother_first, daughter_first,
                     mother_t0=0.0, daughter_t0=divide_t)


if __name__ == '__main__':
    main()
