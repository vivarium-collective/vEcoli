"""Generate a Markdown report comparing v1 vs v2 workflows.

Pulls task durations from each workflow's nextflow trace CSV and
renders analysis plot HTMLs to PNGs staged alongside the report.

By default writes ``doc/v1_v2_report.md`` with assets under
``doc/_static/v1_v2_report_assets/``, so GitHub renders it natively
and images resolve via relative paths. Use ``--out <path>`` to
override the destination.
"""
import argparse
import glob
import json
import os
import re
import shutil

import polars as pl
import vl_convert as vlc


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def load_trace(experiment_id):
    pattern = f'{REPO_ROOT}/trace--{experiment_id}--*.csv'
    files = sorted(glob.glob(pattern))
    if not files:
        return None
    return pl.read_csv(files[-1])


def load_cost_meta(experiment_id):
    """Read the cost_meta JSON sidecar that MP/Ray runners drop next
    to their synthetic trace CSV. Returns ``None`` if absent (i.e. a
    Nextflow run, which doesn't need the sidecar)."""
    candidates = [
        f'{REPO_ROOT}/cost_meta--{experiment_id}.json',
        f'{REPO_ROOT}/out/{experiment_id}/cost_meta--{experiment_id}.json',
    ]
    for path in candidates:
        if os.path.isfile(path):
            with open(path) as f:
                return json.load(f)
    return None


def division_times(experiment_id):
    """{seed: {gen: division_time_seconds}}

    Two data sources, in priority order:

    1. parity_matrix.tsv (if present): the v1_t_max / v2_t_max columns are
       the absolute global time at which each cell divided. This works for
       AWS Batch runs, where the legacy ``division_time.sh`` files aren't
       persisted to S3.

    2. Per-task ``division_time.sh`` files in nextflow_workdirs/ (legacy
       local-run path, kept for backwards compat with two_generations runs).
    """
    out = {}
    # Path 1: parity_matrix.tsv — works for AWS-side runs.
    matrix_path = (f'{REPO_ROOT}/out/parity_matrix__'
                   f'{experiment_id}__{experiment_id}.tsv')
    # Try both v1 and v2 columns; the matrix file is named after both ids,
    # so we have to look for either ordering.
    candidates = glob.glob(f'{REPO_ROOT}/out/parity_matrix__*.tsv')
    for cand in candidates:
        with open(cand) as f:
            header = f.readline().strip().split('\t')
            if 'v1_t_max' not in header:
                continue
            base = os.path.basename(cand).replace('parity_matrix__', '').replace('.tsv', '')
            parts = base.split('__')
            if len(parts) != 2:
                continue
            ids_to_col = {parts[0]: 'v1_t_max', parts[1]: 'v2_t_max'}
            col = ids_to_col.get(experiment_id)
            if col is None:
                continue
            ix = {h: i for i, h in enumerate(header)}
            for line in f:
                cols = line.rstrip('\n').split('\t')
                if len(cols) < len(header):
                    continue
                seed = cols[ix['seed']]
                gen = int(cols[ix['gen']])
                t = cols[ix[col]]
                if t and t != '-':
                    out.setdefault(seed, {})[gen] = float(t)
            if out:
                return out

    # Path 2: legacy local-run path via per-task division_time.sh files.
    workdir_root = f'{REPO_ROOT}/out/{experiment_id}/nextflow/nextflow_workdirs'
    for sh_path in glob.glob(f'{workdir_root}/*/*/.command.sh'):
        if 'ecoli_master_sim.py' not in open(sh_path).read():
            continue
        sh = open(sh_path).read()
        seed_m = re.search(r'--lineage_seed\s+(\S+)', sh)
        outdir_m = re.search(
            r'--daughter_outdir\s+"?\S*?seed=\d+/generation=(\d+)/agent_id=', sh)
        if not (seed_m and outdir_m):
            continue
        seed = seed_m.group(1)
        gen = int(outdir_m.group(1))
        dt_path = os.path.join(os.path.dirname(sh_path), 'division_time.sh')
        if not os.path.exists(dt_path):
            continue
        dt_text = open(dt_path).read()
        dt_m = re.search(r'division_time=([\d.]+)', dt_text)
        if dt_m:
            out.setdefault(seed, {})[gen] = float(dt_m.group(1))
    return out


def find_plot(experiment_id, kind, seed=None, gen=None):
    """Return path to a plot HTML/TSV for the given analysis type."""
    base = f'{REPO_ROOT}/out/{experiment_id}/analyses'
    if seed is not None and gen is not None:
        # agent_id encodes the binary lineage path; for the always-take-
        # daughter-0 lineage (single_daughters=true) it's gen zeros wide.
        agent_id = '0' * int(gen)
        path = (f'{base}/variant=0/lineage_seed={seed}/generation={gen}/'
                f'agent_id={agent_id}/plots/analysis={kind}')
    else:
        path = f'{base}/variant=0/plots/analysis={kind}'
    if not os.path.isdir(path):
        return None
    htmls = glob.glob(f'{path}/*.html')
    if htmls:
        return htmls[0]
    tsvs = glob.glob(f'{path}/*.tsv')
    return tsvs[0] if tsvs else None


VEGA_SPEC_RE = re.compile(r'var spec = (\{.*?\});\s*\n\s*var ', re.DOTALL)


def vegalite_to_png(html_path, out_png_path):
    """Extract the first Vega-Lite spec from a plot HTML, render it to
    PNG via vl-convert. Returns True on success, False if no spec found."""
    src = open(html_path).read()
    m = VEGA_SPEC_RE.search(src)
    if not m:
        return False
    spec = json.loads(m.group(1))
    png_bytes = vlc.vegalite_to_png(spec, scale=1.5)
    with open(out_png_path, 'wb') as f:
        f.write(png_bytes)
    return True


def stage_asset(src_path, assets_dir, assets_rel, stem):
    """Stage the analysis output for the report. Vega-Lite HTMLs are
    rendered to PNG; everything else is copied through. Returns the
    path relative to the report, or None."""
    if src_path is None:
        return None
    if src_path.endswith('.html'):
        png_path = os.path.join(assets_dir, f'{stem}.png')
        if vegalite_to_png(src_path, png_path):
            return f'{assets_rel}/{stem}.png'
        html_name = f'{stem}.html'
        shutil.copyfile(src_path, os.path.join(assets_dir, html_name))
        return f'{assets_rel}/{html_name}'
    ext = os.path.splitext(src_path)[1]
    dest = f'{stem}{ext}'
    shutil.copyfile(src_path, os.path.join(assets_dir, dest))
    return f'{assets_rel}/{dest}'


def md_table(headers, rows):
    def fmt_row(r):
        return '| ' + ' | '.join(str(c) for c in r) + ' |'
    sep = '|' + '|'.join(['---'] * len(headers)) + '|'
    return '\n'.join([fmt_row(headers), sep, *[fmt_row(r) for r in rows]])


def tsv_preview(path, n=10):
    """Read a TSV and render the first n non-empty rows as a markdown table."""
    if path is None or not os.path.exists(path):
        return '_(missing)_'
    with open(path) as f:
        lines = [ln.rstrip('\n') for ln in f if ln.strip()]
    if not lines:
        return '_(empty)_'
    header = lines[0].split('\t')
    rows = [ln.split('\t') for ln in lines[1:1 + n]]
    extra = len(lines) - 1 - len(rows)
    tbl = md_table(header, rows)
    if extra > 0:
        tbl += f'\n\n_… {extra:,} more rows_'
    return tbl


def plot_row(label, engines):
    """Render one analysis as N side-by-side cells, one per engine.

    ``engines`` is a list of ``(engine_label, rel_path, abs_path)``
    tuples. ``rel_path`` is None when the analysis is missing for
    that engine (still gets a column showing _(missing)_ — keeps
    column count consistent across engines).
    """
    def cell(rel, abs_path):
        if rel is None:
            return '_(missing)_'
        if rel.endswith('.png'):
            return f'![{label}]({rel})'
        return f'[{os.path.basename(rel)}]({rel})'
    header = f'### {label}\n'
    # Tabular analyses — render previews stacked rather than side-by-
    # side, so the rows stay readable on narrow viewers.
    is_tabular = any(
        rel and rel.endswith('.tsv') for _, rel, _ in engines)
    if is_tabular:
        parts = [header]
        for eng_label, rel, abs_path in engines:
            parts.append(
                f'**{eng_label}** '
                + (f'([full file]({rel}))\n\n' if rel else '_(missing)_\n\n')
                + (f'{tsv_preview(abs_path)}\n' if rel else ''))
        return '\n'.join(parts)
    # Plot images — one column per engine.
    head_row = '| ' + ' | '.join(eng_label for eng_label, _, _ in engines) + ' |'
    sep_row = '|' + '|'.join(['---'] * len(engines)) + '|'
    cell_row = '| ' + ' | '.join(
        cell(rel, abs_path) for _, rel, abs_path in engines) + ' |'
    return f'{header}\n{head_row}\n{sep_row}\n{cell_row}\n'


def parity_matrix_section(matrix_paths):
    """Render one parity matrix per (v1, other) pair.

    ``matrix_paths`` is a comma-separated list of TSV file paths. Each
    file is named ``parity_matrix__v1__<label>.tsv`` so the section
    title can pull the engine label from the filename.
    """
    if not matrix_paths:
        return ''
    paths = [p for p in matrix_paths.split(',') if p]
    sections = []
    for matrix_path in paths:
        section = _parity_matrix_one(matrix_path)
        if section:
            sections.append(section)
    if not sections:
        return ''
    return '## Bulk parity matrices\n\n' + '\n\n'.join(sections)


def _parity_matrix_one(matrix_path):
    if not os.path.exists(matrix_path):
        return ''
    # Derive engine label from filename: parity_matrix__v1__<label>.tsv
    fname = os.path.basename(matrix_path)
    label = 'v2'
    m = re.match(r'parity_matrix__v1__([^.]+)\.tsv$', fname)
    if m:
        label = m.group(1)
    rows = []
    with open(matrix_path) as f:
        header = f.readline().strip().split('\t')
        for line in f:
            cols = line.rstrip('\n').split('\t')
            if len(cols) < len(header):
                continue
            r = dict(zip(header, cols))
            for k in ('seed', 'gen', 'n_steps', 'n_identical',
                      'first_diff_t', 'max_abs', 'max_l1', 'n_species'):
                r[k] = int(r[k])
            rows.append(r)
    if not rows:
        return ''
    by_cell = {(r['seed'], r['gen']): r for r in rows}
    seeds_l = sorted({r['seed'] for r in rows})
    gens_l = sorted({r['gen'] for r in rows})
    table = '| seed \\\\ gen | ' + ' | '.join(str(g) for g in gens_l) + ' |\n'
    table += '|---' * (len(gens_l) + 1) + '|\n'
    for seed in seeds_l:
        cells = [str(seed)]
        for gen in gens_l:
            r = by_cell.get((seed, gen))
            if r is None:
                cells.append('—')
            elif r['n_identical'] == r['n_steps']:
                cells.append('=')
            else:
                cells.append(f"Δ@{r['first_diff_t']}")
        table += '| ' + ' | '.join(cells) + ' |\n'
    # Worst-case stats
    diverged = [r for r in rows if r['n_identical'] != r['n_steps']]
    legend = (
        '`=` = bit-identical bulk vector at every common timestep. '
        '`Δ@<t>` = first divergence timestep. `—` = missing data.\n\n'
    )
    summary = ''
    if diverged:
        worst_l1 = max(diverged, key=lambda r: r['max_l1'])
        worst_abs = max(diverged, key=lambda r: r['max_abs'])
        first = min(diverged, key=lambda r: (r['first_diff_t'], r['seed'], r['gen']))
        summary = (
            f'\n**Diverged cells:** {len(diverged)} of {len(rows)}.  '
            f'Earliest divergence at seed {first["seed"]} gen {first["gen"]} '
            f'(t={first["first_diff_t"]}). '
            f'Worst max|Δ| = {worst_abs["max_abs"]} '
            f'(seed {worst_abs["seed"]} gen {worst_abs["gen"]}). '
            f'Worst L1 = {worst_l1["max_l1"]} '
            f'(seed {worst_l1["seed"]} gen {worst_l1["gen"]}).\n'
        )
    else:
        summary = f'\n**All {len(rows)} cells bit-identical.**\n'
    return (f'### Bulk parity: v1 vs {label}\n\n' + legend + table + summary
            + f'\n_Source: `{matrix_path}`._\n')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='doc/v1_v2_report.md')
    p.add_argument('--v1-id', default='two_generations_v1',
                   help='v1 experiment id (matches out/<id>/ and trace--<id>--*.csv)')
    p.add_argument('--v2-id', default='two_generations_v2',
                   help='v2 experiment id')
    p.add_argument('--seeds', default='0,1',
                   help='comma-separated seeds for per-cell plots')
    p.add_argument('--gens', default='1,2',
                   help='comma-separated generation ints for per-cell plots')
    p.add_argument('--parity-matrix', default='out/parity_matrix.tsv',
                   help='path to parity_matrix.tsv (rendered if present)')
    p.add_argument(
        '--extra-ids', default='',
        help='Comma-separated additional engine experiment IDs (e.g. '
        'a v2-MP run and a v2-Ray run) to include in the workflow '
        'wall-clock table. Each can be optionally labelled as '
        '``label=experiment_id`` (e.g. '
        '``mp=comparison_10s_16g_v2_mp_aws,ray=comparison_10s_16g_v2_ray_aws``); '
        'an unlabelled id uses the experiment_id itself as the label.')
    p.add_argument(
        '--engine-cost', default='',
        help='Per-engine cost spec for engines that DON\'T emit a '
        'Nextflow trace CSV (mp/ray). Comma-separated entries of the '
        'form ``label=spec`` where spec is one of:'
        '\n  ``single:<instance>:<wall_s>`` — mp_single_node deploy'
        '\n  ``cluster:<head_instance>:<worker_instance>:<n_workers>:<wall_s>``'
        ' — ray_cluster deploy'
        '\nE.g. '
        '``mp=single:c7g.metal:1200,ray=cluster:t4g.large:c7g.metal:4:800``')
    args = p.parse_args()

    seeds = [s.strip() for s in args.seeds.split(',') if s.strip()]
    gens = [int(g.strip()) for g in args.gens.split(',') if g.strip()]

    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    out_stem = os.path.splitext(os.path.basename(out_path))[0]
    # Assets live under doc/_static/<stem>_assets so Sphinx picks them
    # up and GitHub resolves relative paths from doc/v1_v2_report.md.
    assets_rel = f'_static/{out_stem}_assets'
    assets_dir = os.path.join(out_dir, assets_rel)
    if os.path.isdir(assets_dir):
        shutil.rmtree(assets_dir)
    os.makedirs(assets_dir, exist_ok=True)

    v1_trace = load_trace(args.v1_id)
    v2_trace = load_trace(args.v2_id)

    # Per-sim runtime table
    def per_sim_dict(trace):
        out = {}
        if trace is None:
            return out
        for r in trace.iter_rows(named=True):
            name = r['name']
            if not name.startswith('sim_'):
                continue
            m = re.search(r'seed=(\d+)/generation=(\d+)', name)
            if not m:
                continue
            out[(m.group(1), int(m.group(2)))] = r['duration'] / 1000.0
        return out

    def workflow_stats(trace):
        """Compute workflow-level timing stats from the nextflow trace.

        Returns ``{'total_wall_s', 'sim_task_total_s', 'all_task_total_s',
        'per_seed': {seed: (sim_task_total, seed_wall, gap)}}``.

        - ``total_wall_s``: workflow wall-clock end-to-end
          (max(complete) − min(submit)). Includes scheduler overhead
          and inter-task gaps.
        - ``sim_task_total_s``: sum of `sim_*` task durations.
        - ``all_task_total_s``: sum of every task's duration (including
          parca, analysis, etc.).
        - ``per_seed``: for each seed, sum of sim_* durations,
          per-seed wall (latest sim_* complete − earliest sim_*
          submit), and the gap (per-seed wall − sum of sim_*
          durations). Per-seed gen tasks run sequentially, so gap
          ≥ 0 and reflects scheduler/container/queue overhead.
        """
        out = {'total_wall_s': None, 'sim_task_total_s': 0.0,
               'all_task_total_s': 0.0, 'per_seed': {}}
        if trace is None:
            return out
        all_submit, all_complete = None, None
        per_seed_submit, per_seed_complete = {}, {}
        per_seed_sum = {}
        for r in trace.iter_rows(named=True):
            sub = r.get('submit')
            comp = r.get('complete')
            dur = (r.get('duration') or 0) / 1000.0
            if sub is not None:
                all_submit = sub if all_submit is None else min(all_submit, sub)
            if comp is not None:
                all_complete = comp if all_complete is None else max(all_complete, comp)
            out['all_task_total_s'] += dur
            name = r.get('name', '')
            if name.startswith('sim_'):
                out['sim_task_total_s'] += dur
                m = re.search(r'seed=(\d+)/generation=', name)
                if m:
                    seed = m.group(1)
                    per_seed_sum[seed] = per_seed_sum.get(seed, 0.0) + dur
                    if sub is not None:
                        per_seed_submit[seed] = (
                            sub if seed not in per_seed_submit
                            else min(per_seed_submit[seed], sub))
                    if comp is not None:
                        per_seed_complete[seed] = (
                            comp if seed not in per_seed_complete
                            else max(per_seed_complete[seed], comp))
        if all_submit is not None and all_complete is not None:
            out['total_wall_s'] = (all_complete - all_submit) / 1000.0
        for seed, ssum in per_seed_sum.items():
            sub = per_seed_submit.get(seed)
            comp = per_seed_complete.get(seed)
            seed_wall = ((comp - sub) / 1000.0) if (sub and comp) else None
            gap = (seed_wall - ssum) if seed_wall is not None else None
            out['per_seed'][seed] = (ssum, seed_wall, gap)
        return out

    v1_sim = per_sim_dict(v1_trace)
    v2_sim = per_sim_dict(v2_trace)
    v1_wf = workflow_stats(v1_trace)
    v2_wf = workflow_stats(v2_trace)

    # Extra engines (composite_lineage MP, Ray, etc.) that we want
    # included in the workflow-wall-clock table for a 4-way comparison.
    # ``--extra-ids`` is comma-separated, each entry optionally
    # ``label=experiment_id``.
    extra_engines = []
    for raw in args.extra_ids.split(','):
        raw = raw.strip()
        if not raw:
            continue
        label, _, exp_id = raw.partition('=')
        if not exp_id:  # unlabelled — use exp_id as the label
            label, exp_id = raw, raw
        extra_engines.append((label, exp_id, workflow_stats(load_trace(exp_id))))

    # ----- Division-times table (N-way) ----------------------------
    # Columns adapt to the engine set: v1, v2, + each --extra-ids
    # engine. Each engine gets a ``<eng> div_time`` and ``<eng> cycle``
    # column. ``Δ%`` compares each non-v1 engine's cycle to v1's.
    # MP/Ray will mostly show ``-`` until division-time extraction is
    # wired for non-nextflow runs (currently division_times() pulls
    # from daughter_states JSONs / nextflow workdirs).
    engines_for_div = (
        [('v1', args.v1_id), ('v2', args.v2_id)]
        + [(label, exp_id) for (label, exp_id, _) in extra_engines])
    eng_dt = {label: division_times(exp_id) for (label, exp_id) in engines_for_div}
    div_seeds = sorted({s for dt in eng_dt.values() for s in dt})
    div_gens = sorted({g for dt in eng_dt.values()
                       for s in dt for g in dt[s]})
    div_headers = ['Seed', 'Gen']
    for label, _ in engines_for_div:
        div_headers.append(f'{label} div_time')
    for label, _ in engines_for_div:
        div_headers.append(f'{label} cycle')
    for label, _ in engines_for_div:
        if label == 'v1':
            continue
        div_headers.append(f'Δ% ({label}/v1)')

    div_rows = []
    prev_dt = {label: 0.0 for label, _ in engines_for_div}
    for seed in div_seeds:
        # Reset prev cycles per seed
        for label, _ in engines_for_div:
            prev_dt[label] = 0.0
        for gen in div_gens:
            row = [seed, gen]
            d_per_engine = {}
            for label, _ in engines_for_div:
                d = eng_dt[label].get(seed, {}).get(gen)
                d_per_engine[label] = d
                row.append(f'{d:.0f}' if d else '-')
            cycle_per_engine = {}
            for label, _ in engines_for_div:
                d = d_per_engine[label]
                cyc = (d - prev_dt[label]) if d else None
                cycle_per_engine[label] = cyc
                row.append(f'{cyc:.0f}' if cyc else '-')
                if d:
                    prev_dt[label] = d
            v1_cyc = cycle_per_engine.get('v1')
            for label, _ in engines_for_div:
                if label == 'v1':
                    continue
                cyc = cycle_per_engine[label]
                if v1_cyc and cyc:
                    row.append(f'{(cyc - v1_cyc) / v1_cyc * 100:+.1f}%')
                else:
                    row.append('-')
            div_rows.append(row)
    div_table = md_table(div_headers, div_rows)

    # Parse --engine-cost spec for engines without trace CSVs.
    # Map: label → (cost_kind, ...spec...).
    cost_specs: dict[str, tuple] = {}
    for raw in args.engine_cost.split(','):
        raw = raw.strip()
        if not raw:
            continue
        label, _, spec = raw.partition('=')
        parts = spec.split(':')
        if not parts:
            continue
        kind = parts[0]
        try:
            if kind == 'single' and len(parts) == 3:
                # single:<instance>:<wall_s>
                cost_specs[label] = (kind, parts[1], float(parts[2]))
            elif kind == 'cluster' and len(parts) == 5:
                # cluster:<head>:<worker>:<n_workers>:<wall_s>
                cost_specs[label] = (
                    kind, parts[1], parts[2], int(parts[3]), float(parts[4]))
            else:
                print(f'warn: unrecognized --engine-cost spec '
                      f'{raw!r}; skipping')
        except (ValueError, IndexError) as e:
            print(f'warn: bad --engine-cost spec {raw!r}: {e}')

    # ----- Per-sim runtime table (N-way) ---------------------------
    # One column per engine: wall (s), s/tick, and a Δ% vs v1.
    # Engines with no trace CSV show ``-`` rather than being dropped.
    eng_sim = {label: per_sim_dict(load_trace(exp_id))
               for (label, exp_id) in engines_for_div}
    eng_sim['v1'] = v1_sim
    eng_sim['v2'] = v2_sim
    has_any_sim = any(s for s in eng_sim.values())
    if not has_any_sim:
        runtime_table = '_No trace CSVs available for any engine yet._\n'
    else:
        rt_headers = ['Sim']
        for label, _ in engines_for_div:
            rt_headers.append(f'{label} wall (s)')
        for label, _ in engines_for_div:
            rt_headers.append(f'{label} s/tick')
        for label, _ in engines_for_div:
            if label == 'v1':
                continue
            rt_headers.append(f'Δ% ({label}/v1)')

        # Aggregate seeds × gens that any engine has data for.
        all_seeds = sorted({s for sim in eng_sim.values() for s, _ in sim})
        runtime_rows = []
        eng_total = {label: 0.0 for label, _ in engines_for_div}
        for seed in all_seeds:
            prev_dt_local = {label: 0.0 for label, _ in engines_for_div}
            all_gens_for_seed = sorted(
                {g for sim in eng_sim.values()
                 for s, g in sim if s == seed})
            for gen in all_gens_for_seed:
                walls = {}
                ticks = {}
                for label, _ in engines_for_div:
                    walls[label] = eng_sim[label].get((seed, gen))
                    d = eng_dt[label].get(seed, {}).get(gen)
                    ticks[label] = (d - prev_dt_local[label]) if d else None
                    if d:
                        prev_dt_local[label] = d
                row = [f'seed {seed} gen {gen}']
                for label, _ in engines_for_div:
                    w = walls[label]
                    row.append(f'{w:.0f}' if w else '-')
                for label, _ in engines_for_div:
                    w, t = walls[label], ticks[label]
                    per = (w / t) if (w and t) else None
                    row.append(f'{per:.3f}' if per else '-')
                v1_w = walls.get('v1')
                for label, _ in engines_for_div:
                    if label == 'v1':
                        continue
                    w = walls[label]
                    if v1_w and w:
                        row.append(f'{(w - v1_w) / v1_w * 100:+.1f}%')
                    else:
                        row.append('-')
                runtime_rows.append(row)
                for label, _ in engines_for_div:
                    if walls[label]:
                        eng_total[label] += walls[label]
        # Total row
        if any(eng_total.values()):
            row = ['**SIM TOTAL**']
            for label, _ in engines_for_div:
                t = eng_total[label]
                row.append(f'**{t:.0f}**' if t else '-')
            for _ in engines_for_div:
                row.append('-')  # s/tick aggregate not meaningful
            v1_total = eng_total.get('v1', 0.0)
            for label, _ in engines_for_div:
                if label == 'v1':
                    continue
                t = eng_total[label]
                if v1_total and t:
                    row.append(f'**{(t - v1_total) / v1_total * 100:+.1f}%**')
                else:
                    row.append('-')
            runtime_rows.append(row)
        runtime_table = md_table(rt_headers, runtime_rows)

    # Analysis plots — N-way: v1 + v2 + each --extra-ids entry. Engine
    # column count adapts to whatever is being compared. Engines whose
    # workflow hasn't emitted analyses yet (e.g. MP/Ray during a still-
    # running run) get a ``_(missing)_`` cell so the column lineup
    # remains stable across rows.
    engine_specs = (
        [('v1', args.v1_id), ('v2', args.v2_id)]
        + [(label, exp_id) for (label, exp_id, _wf) in extra_engines])
    plot_blocks = []
    for seed in seeds:
        for gen in gens:
            cells = []
            for eng_label, exp_id in engine_specs:
                p = find_plot(
                    exp_id, 'mass_fraction_summary', seed, gen)
                r = stage_asset(
                    p, assets_dir, assets_rel,
                    f'mass_fraction_summary__seed{seed}_gen{gen}_{eng_label}')
                cells.append((eng_label, r, p))
            plot_blocks.append(plot_row(
                f'mass_fraction_summary — seed {seed}, gen {gen}', cells))
    for kind in ['protein_counts_validation',
                 'subgenerational_expression_table',
                 'ecocyc_table']:
        cells = []
        for eng_label, exp_id in engine_specs:
            p = find_plot(exp_id, kind)
            r = stage_asset(p, assets_dir, assets_rel, f'{kind}_{eng_label}')
            cells.append((eng_label, r, p))
        plot_blocks.append(plot_row(f'{kind} (multiseed)', cells))

    parity_md = parity_matrix_section(args.parity_matrix)

    # ---- Workflow-total / scheduler-gap section -----------------
    def fmt_s(x):
        return f'{x:.0f}' if x is not None else '-'

    def fmt_pct(num, den):
        if num is None or den in (None, 0):
            return '-'
        return f'{num / den * 100:+.1f}%'

    wf_section_lines = []
    has_any_wall = (v1_wf['total_wall_s'] or v2_wf['total_wall_s']
                    or any(e[2]['total_wall_s'] for e in extra_engines))
    if has_any_wall:
        # Engine-comparison table: one row per engine (v1, v2, +extras)
        # with workflow wall-clock, sum-of-sim-tasks, and the implied
        # parallel-fanout factor. Shape works for 2-way (v1 vs v2) and
        # N-way (add MP, Ray as extras).
        all_engines = (
            [('v1 nextflow', args.v1_id, v1_wf),
             ('v2 nextflow', args.v2_id, v2_wf)]
            + [(label, exp_id, wf) for (label, exp_id, wf) in extra_engines])

        # Find the slowest workflow wall as the baseline for "%" deltas
        baseline = next(
            (e[2]['total_wall_s'] for e in all_engines if e[2]['total_wall_s']),
            None)

        # Cost lookup priority:
        #   1. CLI --engine-cost spec (manual override)
        #   2. cost_meta--<exp_id>.json sidecar (emitted by MP/Ray
        #      runners alongside their synthetic trace)
        #   3. Default: nextflow per-task billing from trace CSV
        from runscripts import cost as cost_mod

        def engine_cost(label, exp_id, wf, trace):
            spec = cost_specs.get(label)
            wall_s = wf.get('total_wall_s') or 0.0
            if spec is not None:
                kind = spec[0]
                if kind == 'single':
                    _, instance, override_wall = spec
                    return cost_mod.single_node_cost(
                        override_wall, instance=instance, on_demand=True)
                if kind == 'cluster':
                    _, head, worker, nw, override_wall = spec
                    return cost_mod.cluster_cost(
                        override_wall, head_instance=head,
                        worker_instance=worker, n_workers=nw,
                        head_on_demand=True, worker_on_demand=True)
            meta = load_cost_meta(exp_id)
            if meta is not None:
                # Prefer the wall written by the runner (start→end of
                # parent process); fall back to trace-derived wall.
                meta_wall = meta.get('workflow_wall_s', wall_s) or wall_s
                mode = meta.get('deploy_mode')
                if mode == 'mp_single_node':
                    return cost_mod.single_node_cost(
                        meta_wall,
                        instance=meta.get('instance', 'c7g.metal'),
                        on_demand=meta.get('on_demand', True))
                if mode == 'ray_cluster':
                    return cost_mod.cluster_cost(
                        meta_wall,
                        head_instance=meta.get('head_instance', 't4g.large'),
                        worker_instance=meta.get('worker_instance', 'c7g.metal'),
                        n_workers=meta.get('n_workers', 1),
                        head_on_demand=meta.get('head_on_demand', True),
                        worker_on_demand=meta.get('worker_on_demand', True))
            # Default: nextflow per-task billing
            return cost_mod.nextflow_cost(trace, head_wall_s=wall_s)

        eng_rows = []
        for (label, exp_id, wf) in all_engines:
            wall = wf['total_wall_s']
            sim_sum = wf['sim_task_total_s']
            # Sum / wall — > 1 means tasks ran concurrently; 1 means
            # one sequential process; only meaningful for nextflow.
            fanout = (sim_sum / wall) if (sim_sum and wall) else None
            delta = (
                (wall - baseline) / baseline * 100
                if (wall is not None and baseline) else None)
            # Resolve trace for this engine (v1, v2, or extra)
            if exp_id == args.v1_id:
                t = v1_trace
            elif exp_id == args.v2_id:
                t = v2_trace
            else:
                t = load_trace(exp_id)
            usd, breakdown = engine_cost(label, exp_id, wf, t)
            eng_rows.append([
                f'{label}<br>`{exp_id}`',
                fmt_s(wall),
                fmt_s(sim_sum),
                fmt_s(wf['all_task_total_s']),
                f'{fanout:.1f}×' if fanout else '-',
                f'{delta:+.1f}%' if delta is not None else '-',
                f'${usd:.2f}' if usd > 0 else '-',
                breakdown,
            ])
        wf_section_lines.append(md_table(
            ['Engine', 'Wall-clock (s)', 'Σ sim_* tasks (s)',
             'Σ all tasks (s)', 'Sim parallelism', 'Δ wall %',
             'Cost (USD)', 'Cost breakdown'],
            eng_rows))
        wf_section_lines.append('')
        wf_section_lines.append(
            '_`Wall-clock` is end-to-end workflow time (max(complete) − '
            'min(submit) across all tasks); includes scheduler/container '
            'overhead. `Σ sim_* tasks` is the in-process compute consumed '
            'across all per-gen sim tasks (cost proxy). `Sim parallelism` '
            '= Σ tasks / wall — >1× means seeds ran concurrently. For '
            'composite_lineage / MP / Ray paths the whole lineage runs in '
            'one task, so wall ≈ Σ tasks (parallelism=1×) but the wall is '
            'dramatically lower because there is no per-gen process '
            'restart. `Δ wall %` is vs the first engine listed. `Cost` '
            'estimates: nextflow uses per-task billing (sum of '
            'duration × rate(cpu_model) at GovCloud Spot, plus head node '
            'On-Demand for full wall); MP / Ray use a single-spec model '
            'fed via `--engine-cost`. Rates from `runscripts/cost.py` — '
            'GovCloud us-gov-west-1, May 2026 snapshot._')
        wf_section_lines.append('')

        # Per-seed gap table — sequential gens within a lineage,
        # so wall ≥ sum and the difference is scheduler overhead.
        # Show all engines side-by-side (v1, v2, + extras).
        all_engines_for_seed = (
            [('v1', v1_wf), ('v2', v2_wf)]
            + [(label, wf) for (label, _, wf) in extra_engines])
        all_seeds = sorted(set().union(*(set(wf['per_seed'])
                                          for _, wf in all_engines_for_seed)))
        seed_rows = []
        for seed in all_seeds:
            row = [seed]
            for _, wf in all_engines_for_seed:
                ssum, swall, sgap = wf['per_seed'].get(seed, (None, None, None))
                row.extend([fmt_s(ssum), fmt_s(swall), fmt_s(sgap)])
            seed_rows.append(row)
        if seed_rows:
            wf_section_lines.append(
                '### Per-seed wall-clock vs task-time')
            wf_section_lines.append('')
            headers = ['Seed']
            for (label, _) in all_engines_for_seed:
                headers.extend([f'{label} Σtasks',
                                f'{label} wall',
                                f'{label} gap'])
            wf_section_lines.append(md_table(headers, seed_rows))
            wf_section_lines.append('')
            wf_section_lines.append(
                '_`gap = wall − sum of sim_* task durations`. '
                'Within a seed, gens run sequentially, so the gap is '
                'inter-task scheduler/container time (Nextflow overhead). '
                'composite_lineage runs the whole lineage in one '
                'process: gap should be ~0._')
    workflow_section = (
        ('## Workflow wall-clock vs task-time\n\n'
         + '\n'.join(wf_section_lines) + '\n\n')
        if wf_section_lines else '')

    md = (
        f'# vEcoli v1 vs v2 — {args.v1_id} vs {args.v2_id}\n\n'
        '_Generated from latest workflow runs by `runscripts/v1_v2_report.py`._\n\n'
        + (parity_md + '\n' if parity_md else '')
        + '## Cell cycle / division times\n\n'
        f'{div_table}\n\n'
        + workflow_section
        + '## Runtime per task (sum across instances)\n\n'
        f'{runtime_table}\n\n'
        '## Analysis plots\n\n'
        + '\n'.join(plot_blocks) + '\n'
    )

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(md)
    print(f'Report written: {out_path}')
    print(f'Assets dir:    {assets_dir}')


if __name__ == '__main__':
    main()
