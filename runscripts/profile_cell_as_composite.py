"""Profile cell-as-Composite vs naked Composite for the SAME cell tree,
to isolate where the wrapper overhead is going.

Each Composite has built-in ``timing_summary()`` that splits ``process_time``
(real sim work) from ``framework_time`` (view/project/apply/realize).
We compare:

  A) Naked: cell_doc → Composite directly, run 10 sim sec
  B) Wrapped: cell_doc → cell-Composite → outer Composite, run 10 sim sec

Same inner cell, same sim time. Any wall delta is wrapper overhead.
We also instrument read_bridge to count calls & total bridge-view size.
"""
import os
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('NUMBA_NUM_THREADS', '1')
try:
    from threadpoolctl import threadpool_limits as _tpl
    _tpl(limits=1)
except ImportError:
    pass

import argparse
import time
from copy import deepcopy


def _instrument_read_bridge(Composite):
    """Wrap Composite.read_bridge to count calls + total payload bytes."""
    import sys
    orig = Composite.read_bridge
    stats = {'calls': 0, 'total_bytes': 0, 'non_empty': 0, 'instances': {}}

    def sizeof_recursive(o, _seen=None):
        if _seen is None:
            _seen = set()
        oid = id(o)
        if oid in _seen:
            return 0
        _seen.add(oid)
        s = sys.getsizeof(o)
        if isinstance(o, dict):
            for k, v in o.items():
                s += sys.getsizeof(k) + sizeof_recursive(v, _seen)
        elif isinstance(o, (list, tuple)):
            for v in o:
                s += sizeof_recursive(v, _seen)
        return s

    def wrapped(self, state=None):
        out = orig(self, state)
        stats['calls'] += 1
        if out is not None:
            sz = sizeof_recursive(out)
            stats['total_bytes'] += sz
            if out:
                stats['non_empty'] += 1
                key = id(self)
                inst = stats['instances'].setdefault(
                    key, {'calls': 0, 'non_empty': 0, 'max_keys': 0,
                          'sample_keys': set()})
                inst['calls'] += 1
                if isinstance(out, dict):
                    inst['non_empty'] += 1
                    inst['max_keys'] = max(inst['max_keys'], len(out))
                    inst['sample_keys'].update(list(out.keys())[:10])
        return out
    Composite.read_bridge = wrapped
    return stats


def _print_timing(label, summary, bridge_stats=None):
    total = summary.total
    proc = summary.process_time
    fw = summary.framework_time
    print(f'\n=== {label} ===')
    print(f'  total wall   : {total:7.3f}s')
    print(f'  process_time : {proc:7.3f}s  ({100*proc/total:.1f}%)')
    print(f'  framework_time: {fw:7.3f}s  ({100*fw/total:.1f}%)')
    print(f'  top processes by invoke time:')
    top = sorted(summary.per_process.items(), key=lambda kv: -kv[1])[:8]
    for path, t in top:
        print(f'    {t:7.3f}s  {path}')
    if bridge_stats is not None:
        print(f'  bridge: {bridge_stats["calls"]} read_bridge calls, '
              f'{bridge_stats["non_empty"]} non-empty, '
              f'{bridge_stats["total_bytes"]/1024:.1f} KiB total')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--sim-data-path', default='out/kb/simData.cPickle')
    ap.add_argument('--duration', type=float, default=10.0)
    args = ap.parse_args()

    sim_data_path = os.path.abspath(args.sim_data_path)

    from configs import CONFIG_DIR_PATH
    from ecoli.experiments.ecoli_master_sim import EcoliSim
    from ecoli.library import bigraph_types as _bt
    from ecoli.library.bigraph_types import ECOLI_TYPES
    from ecoli.library.sim_data import LoadSimData
    from ecoli.composites.ecoli_composite import build_ecoli_document
    from process_bigraph import Composite, allocate_core

    # Instrument read_bridge GLOBALLY (affects all Composites in process)
    bridge_stats = _instrument_read_bridge(Composite)

    sim = EcoliSim.from_file(os.path.join(CONFIG_DIR_PATH, 'default.json'))
    sim.processes = sim._retrieve_processes(
        sim.processes, sim.add_processes,
        sim.exclude_processes, sim.swap_processes)
    sim.topology = sim._retrieve_topology(
        sim.topology, sim.processes, sim.swap_processes, sim.log_updates)
    sim.process_configs = sim._retrieve_process_configs(
        sim.process_configs, sim.processes)
    sim_config = dict(sim.config)
    sim_config['sim_data_path'] = sim_data_path
    sim_config['agent_id'] = '0'
    sim_config['seed'] = 0
    sim_config['divide'] = True

    # ===================================================
    # A) Build NAKED composite (greenfield-style, wrapped agents)
    # ===================================================
    print('[profile] building NAKED greenfield composite...', flush=True)
    core_a = allocate_core()
    core_a.register_types(ECOLI_TYPES)
    core_a.register_link('Composite', Composite)
    lsd_a = LoadSimData(**{**sim_config, 'seed': 0})
    t0 = time.perf_counter()
    doc_a = build_ecoli_document(core_a, sim_config, load_sim_data=lsd_a)
    naked = Composite({'state': doc_a, 'schema': {}}, core=core_a)
    print(f'[profile]   naked built in {time.perf_counter()-t0:.1f}s', flush=True)
    print(f'[profile]   naked.process_paths: {len(naked.process_paths)}, '
          f'step_paths: {len(naked.step_paths)}', flush=True)

    # Reset bridge stats just before run
    bridge_stats['calls'] = 0
    bridge_stats['total_bytes'] = 0
    bridge_stats['non_empty'] = 0
    bridge_stats['instances'].clear()
    print(f'[profile] running NAKED for {args.duration}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    naked.run(args.duration)
    naked_wall = time.perf_counter() - t0
    naked_bridge = dict(bridge_stats)
    naked_summary = naked.timing_summary()
    _print_timing('NAKED greenfield', naked_summary, naked_bridge)
    print(f'  wall (perf_counter): {naked_wall:.3f}s', flush=True)

    # ===================================================
    # B) Build WRAPPED cell-as-Composite. Patch is now upstream in
    #    process_bigraph.composite.Composite.update (skip merge when
    #    project_state is empty).
    # ===================================================
    print('\n[profile] building WRAPPED cell-as-Composite '
          '(upstream patch active)...', flush=True)
    core_b = allocate_core()
    core_b.register_types(ECOLI_TYPES)
    core_b.register_link('Composite', Composite)
    lsd_b = LoadSimData(**{**sim_config, 'seed': 0})
    cell_doc = build_ecoli_document(core_b, sim_config, load_sim_data=lsd_b,
                                     flat=True)
    sd_store = cell_doc.get('sim_data_objects', {})
    for k, v in sd_store.items():
        if not k.startswith('_'):
            _bt._sim_data_object_instances[k] = v

    cell_node = {
        '_type': 'process',
        'address': 'local:Composite',
        'config': {
            'state': cell_doc,
            'bridge': {
                'outputs': {
                    'agents': ['agents'],
                },
            },
            'run_steps_on_init': True,
        },
        'inputs': {},
        'outputs': {'agents': ['..']},
        'interval': 1.0,
    }
    outer_state = {'agents': {'0': {'cell': cell_node}}}
    t0 = time.perf_counter()
    outer = Composite(
        {'state': outer_state,
         'schema': {'agents': {'_type': 'map', '_value': {'cell': 'process'}}}},
        core=core_b,
    )
    print(f'[profile]   wrapped built in {time.perf_counter()-t0:.1f}s',
          flush=True)
    # Grab the inner cell Composite instance for its own timing later
    cell_inner = outer.state['agents']['0']['cell']['instance']
    print(f'[profile]   outer process_paths: {len(outer.process_paths)}, '
          f'step_paths: {len(outer.step_paths)}', flush=True)
    print(f'[profile]   inner cell process_paths: {len(cell_inner.process_paths)}, '
          f'step_paths: {len(cell_inner.step_paths)}', flush=True)

    bridge_stats['calls'] = 0
    bridge_stats['total_bytes'] = 0
    bridge_stats['non_empty'] = 0
    bridge_stats['instances'].clear()
    print(f'[profile] running WRAPPED for {args.duration}s sim time...',
          flush=True)
    t0 = time.perf_counter()
    outer.run(args.duration)
    wrapped_wall = time.perf_counter() - t0
    wrapped_bridge = dict(bridge_stats)
    # outer's timing_summary reflects the outer run, but its
    # process_time INCLUDES the time spent inside cell.update (since
    # cell is invoked as a process). cell.timing_summary() shows the
    # inner split.
    outer_summary = outer.timing_summary()
    inner_summary = cell_inner.timing_summary()
    _print_timing('WRAPPED outer (cell is one "process")', outer_summary)
    _print_timing('WRAPPED inner cell', inner_summary, wrapped_bridge)
    print(f'  outer wall (perf_counter): {wrapped_wall:.3f}s', flush=True)

    # ===================================================
    # Compare
    # ===================================================
    print('\n=== COMPARISON ===')
    print(f'  naked wall  : {naked_wall:.3f}s')
    print(f'  wrapped wall: {wrapped_wall:.3f}s '
          f'(overhead: {wrapped_wall - naked_wall:+.3f}s, '
          f'{wrapped_wall/naked_wall:.2f}x)')
    print(f'  naked  process_time: {naked_summary.process_time:.3f}s')
    print(f'  inner  process_time: {inner_summary.process_time:.3f}s '
          f'(should be ~same as naked, both did same sim work)')
    print(f'  outer  framework_time: {outer_summary.framework_time:.3f}s')
    print(f'  inner  framework_time: {inner_summary.framework_time:.3f}s')
    print(f'  naked  framework_time: {naked_summary.framework_time:.3f}s')
    extra_fw = (outer_summary.framework_time
                + inner_summary.framework_time
                - naked_summary.framework_time)
    print(f'  extra framework (outer+inner-naked): {extra_fw:.3f}s')

    # Bridge details
    if wrapped_bridge['non_empty'] > 0:
        avg_bytes = wrapped_bridge['total_bytes'] / wrapped_bridge['non_empty']
        print(f'  bridge: {wrapped_bridge["calls"]} reads, '
              f'{wrapped_bridge["non_empty"]} non-empty, '
              f'{wrapped_bridge["total_bytes"]/1024:.1f} KiB total, '
              f'{avg_bytes:.0f} bytes avg non-empty')
    else:
        print(f'  bridge: {wrapped_bridge["calls"]} reads, all empty (good)')
    for inst_id, inst in wrapped_bridge['instances'].items():
        print(f'    inst {inst_id}: {inst["calls"]} reads, '
              f'max_keys={inst["max_keys"]}, '
              f'sample_keys={sorted(inst["sample_keys"])[:5]}')


if __name__ == '__main__':
    main()
