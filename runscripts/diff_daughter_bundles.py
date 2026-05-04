"""Diff a V1 daughter state JSON against a V2 daughter bundle's document.json.

V1 saves daughters as a single JSON (``daughter_state_N.json``); V2 saves
them as a bundle dir with ``document.json`` + arrays/. This script walks
both side-by-side and prints fields where they differ.

For numeric arrays/scalars: reports max-abs diff, max-rel diff, and a
preview. For dicts: recurses. For lists: zips index-wise.

Usage:
  uv run python runscripts/diff_daughter_bundles.py \\
      out/two_generations_v1/daughter_states/.../daughter_state_0.json \\
      out/two_generations_v2/daughter_states/.../daughter_state_0/document.json

Optional: pass --min-diff <value> to suppress near-zero diffs.
"""
import argparse
import json
import os
import sys
from typing import Any


def load_v1(path):
    with open(path) as f:
        return json.load(f)


def load_v2_doc(path):
    with open(path) as f:
        return json.load(f)


def cell_state(doc):
    """Drop into the cell-state level, regardless of wrapping.
    Both V1 and V2 wrap as ``{agents: {agent_id: cell_state}}``;
    V2 also wraps that inside ``{state: ...}``. Strip both."""
    if isinstance(doc, dict) and 'state' in doc and 'agents' in doc.get('state', {}):
        doc = doc['state']
    if isinstance(doc, dict) and 'agents' in doc:
        agents = doc['agents']
        if isinstance(agents, dict) and agents:
            return next(iter(agents.values()))
    return doc


def is_scalar(x):
    return isinstance(x, (int, float, bool, str)) or x is None


def numeric_diff(a, b):
    """For a pair of numeric scalars/lists, return (max_abs, max_rel)."""
    try:
        import numpy as np
        aa = np.asarray(a, dtype=float).ravel()
        bb = np.asarray(b, dtype=float).ravel()
        if aa.shape != bb.shape:
            return None
        if aa.size == 0:
            return (0.0, 0.0)
        d = np.abs(aa - bb)
        denom = np.maximum(np.abs(aa), np.abs(bb))
        denom[denom == 0] = 1
        return (float(d.max()), float((d / denom).max()))
    except Exception:
        return None


# Paths whose divergence is expected/structural — skip them so the
# meaningful diffs aren't drowned out. These are V2-only top-level
# keys that hold process declarations / V2 framework bookkeeping that
# V1 doesn't save in its daughter JSON.
V2_ONLY_TOP_KEYS = {
    'allocator_rng',  # Cell-level RNG store, V1 stores inside process
    'bulk-timeline',  # Process declaration
    'division',  # Process declaration
    'global_clock',  # Process declaration
    'mark_d_period',  # Process declaration
    'media_update',  # Process declaration
    'post-division-mass-listener',  # Process declaration
    'process',  # Per-PartitionedProcess SharedProcess decls (v2 only)
    'step_flow',  # Step layering tokens
    'unique_molecule_counts',  # Process declaration
    # All process top-level decl keys
}

# Top-level key prefixes that are V2 process declarations (skip)
V2_DECL_PREFIXES = (
    'ecoli-',
    'allocator_',
    'unique_update_',
    'mark_d_period',
    'monomer_counts_',
    'rna_synth_prob_',
    'RNA_counts_',
    'dna_supercoiling_',
    'replication_data_',
)

# Substrings — paths containing these are skipped
SKIP_SUBSTRINGS = (
    '__numpy__',  # Numpy serialization metadata
    '__structured_array__',
    'instance',  # process instances (v1) or process state (v2)
    'address',  # process addresses
)


def is_v2_decl_key(key):
    if key in V2_ONLY_TOP_KEYS:
        return True
    if isinstance(key, str):
        for p in V2_DECL_PREFIXES:
            if key.startswith(p):
                return True
    return False


def should_skip(path):
    # Top-level V2 declaration keys (V1 doesn't save them)
    if path and is_v2_decl_key(path[0]):
        return True
    for seg in path:
        for sub in SKIP_SUBSTRINGS:
            if isinstance(seg, str) and sub in seg:
                return True
    return False


def walk(v1, v2, path=(), out=None, min_diff=0.0):
    if out is None:
        out = []

    if should_skip(path):
        return out

    # Both missing → ok
    if v1 is None and v2 is None:
        return out

    # One missing
    if v1 is None:
        out.append((path, 'V1_MISSING', None, type(v2).__name__))
        return out
    if v2 is None:
        out.append((path, 'V2_MISSING', type(v1).__name__, None))
        return out

    # Type mismatch
    t1, t2 = type(v1).__name__, type(v2).__name__
    if t1 != t2 and not (
        is_scalar(v1) and is_scalar(v2)
    ):
        out.append((path, 'TYPE_DIFF', t1, t2))
        return out

    # Dict — recurse
    if isinstance(v1, dict):
        keys = set(v1.keys()) | set(v2.keys())
        for k in sorted(keys, key=str):
            walk(v1.get(k), v2.get(k), path + (k,), out, min_diff)
        return out

    # List — zip index-wise
    if isinstance(v1, list):
        if len(v1) != len(v2):
            out.append((path, 'LIST_LEN_DIFF', len(v1), len(v2)))
        for i, (a, b) in enumerate(zip(v1, v2)):
            walk(a, b, path + (i,), out, min_diff)
        return out

    # Scalars
    if is_scalar(v1) and is_scalar(v2):
        if v1 == v2:
            return out
        # Numeric diff
        if isinstance(v1, (int, float, bool)) and isinstance(v2, (int, float, bool)):
            d = numeric_diff(v1, v2)
            if d is not None:
                ad, rd = d
                if ad < min_diff:
                    return out
            out.append((path, 'VAL', v1, v2))
        else:
            # Strings or mixed
            out.append((path, 'VAL', v1, v2))
        return out

    out.append((path, 'OTHER', repr(v1)[:60], repr(v2)[:60]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('v1_json')
    ap.add_argument('v2_doc')
    ap.add_argument('--min-diff', type=float, default=0.0,
                    help='Suppress numeric diffs below this absolute value.')
    ap.add_argument('--max-rows', type=int, default=200)
    args = ap.parse_args()

    v1_raw = load_v1(args.v1_json)
    v2_raw = load_v2_doc(args.v2_doc)
    v1 = cell_state(v1_raw)
    v2 = cell_state(v2_raw)

    diffs = walk(v1, v2, min_diff=args.min_diff)
    print(f'\n{len(diffs)} divergent fields\n')

    # Group by top-level path segment for readability
    by_top = {}
    for d in diffs:
        top = '.'.join(map(str, d[0][:2])) if len(d[0]) >= 2 else (
            str(d[0][0]) if d[0] else '<root>')
        by_top.setdefault(top, []).append(d)

    print(f'{"top":<35} {"count":>6}')
    print('-' * 50)
    for top in sorted(by_top, key=lambda k: -len(by_top[k])):
        print(f'{top:<35} {len(by_top[top]):>6}')

    print(f'\nFirst {args.max_rows} diffs (sorted by path):')
    print('-' * 90)
    for path, kind, a, b in sorted(diffs, key=lambda d: tuple(map(str, d[0])))[:args.max_rows]:
        path_str = '.'.join(map(str, path))
        if len(path_str) > 60:
            path_str = '...' + path_str[-57:]
        a_str = repr(a)[:30]
        b_str = repr(b)[:30]
        print(f'  {kind:<14} {path_str:<60} V1={a_str:<30} V2={b_str}')


if __name__ == '__main__':
    main()
