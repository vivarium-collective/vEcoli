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


def plot_row(label, v1_rel, v2_rel, v1_abs, v2_abs):
    def cell(rel, abs_path):
        if rel is None:
            return '_(missing)_'
        if rel.endswith('.png'):
            return f'![{label}]({rel})'
        if rel.endswith('.tsv'):
            return f'[{os.path.basename(rel)}]({rel})'
        return f'[{os.path.basename(rel)}]({rel})'
    header = f'### {label}\n'
    # Tabular analyses — render previews stacked, not side-by-side, so
    # the rows stay readable.
    if (v1_rel and v1_rel.endswith('.tsv')) or \
       (v2_rel and v2_rel.endswith('.tsv')):
        return (f'{header}\n'
                f'**V1** ([full file]({v1_rel}))\n\n'
                f'{tsv_preview(v1_abs)}\n\n'
                f'**V2** ([full file]({v2_rel}))\n\n'
                f'{tsv_preview(v2_abs)}\n')
    # Plot images — side-by-side so V1/V2 are easy to compare.
    return (f'{header}\n'
            f'| V1 | V2 |\n|---|---|\n'
            f'| {cell(v1_rel, v1_abs)} | {cell(v2_rel, v2_abs)} |\n')


def parity_matrix_section(matrix_path):
    """Render parity_matrix.tsv as a per-(seed, gen) markdown matrix."""
    if not os.path.exists(matrix_path):
        return ''
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
    return ('## Bulk parity matrix\n\n' + legend + table + summary
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

    v1_dt = division_times(args.v1_id)
    v2_dt = division_times(args.v2_id)

    # Division times table
    div_rows = []
    for seed in sorted(set(v1_dt) | set(v2_dt)):
        prev_v1, prev_v2 = 0.0, 0.0
        for gen in sorted(set(v1_dt.get(seed, {})) | set(v2_dt.get(seed, {}))):
            v1d = v1_dt.get(seed, {}).get(gen)
            v2d = v2_dt.get(seed, {}).get(gen)
            v1_cycle = (v1d - prev_v1) if v1d else None
            v2_cycle = (v2d - prev_v2) if v2d else None
            delta_pct = ((v2_cycle - v1_cycle) / v1_cycle * 100) \
                if v1_cycle and v2_cycle else None
            div_rows.append([
                seed, gen,
                f'{v1d:.0f}' if v1d else '-',
                f'{v2d:.0f}' if v2d else '-',
                f'{v1_cycle:.0f}' if v1_cycle else '-',
                f'{v2_cycle:.0f}' if v2_cycle else '-',
                f'{delta_pct:+.1f}%' if delta_pct is not None else '-',
            ])
            if v1d:
                prev_v1 = v1d
            if v2d:
                prev_v2 = v2d
    div_table = md_table(
        ['Seed', 'Gen', 'V1 div_time', 'V2 div_time',
         'V1 cycle', 'V2 cycle', 'Δ%'],
        div_rows)

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

    runtime_rows = []
    v1_sim_total = 0.0
    v2_sim_total = 0.0
    for seed in sorted(set(s for s, _ in v1_sim) | set(s for s, _ in v2_sim)):
        prev_v1, prev_v2 = 0.0, 0.0
        for gen in sorted(set(g for s, g in v1_sim if s == seed) |
                          set(g for s, g in v2_sim if s == seed)):
            v1_wall = v1_sim.get((seed, gen))
            v2_wall = v2_sim.get((seed, gen))
            v1_div = v1_dt.get(seed, {}).get(gen)
            v2_div = v2_dt.get(seed, {}).get(gen)
            v1_ticks = (v1_div - prev_v1) if v1_div else None
            v2_ticks = (v2_div - prev_v2) if v2_div else None
            v1_per = (v1_wall / v1_ticks) if v1_wall and v1_ticks else None
            v2_per = (v2_wall / v2_ticks) if v2_wall and v2_ticks else None
            delta = ((v2_wall - v1_wall) / v1_wall * 100) \
                if v1_wall and v2_wall else None
            runtime_rows.append([
                f'seed {seed} gen {gen}',
                f'{v1_wall:.0f}' if v1_wall else '-',
                f'{v2_wall:.0f}' if v2_wall else '-',
                f'{v1_per:.3f}' if v1_per else '-',
                f'{v2_per:.3f}' if v2_per else '-',
                f'{delta:+.1f}%' if delta is not None else '-',
            ])
            if v1_wall:
                v1_sim_total += v1_wall
                prev_v1 = v1_div or prev_v1
            if v2_wall:
                v2_sim_total += v2_wall
                prev_v2 = v2_div or prev_v2
    if v1_sim_total and v2_sim_total:
        total_delta = (v2_sim_total - v1_sim_total) / v1_sim_total * 100
        runtime_rows.append([
            '**SIM TOTAL**',
            f'**{v1_sim_total:.0f}**',
            f'**{v2_sim_total:.0f}**',
            '-', '-',
            f'**{total_delta:+.1f}%**',
        ])
    if not v1_sim and v2_sim:
        runtime_table = (
            '_V1 trace CSV not available — atlantis-driven runs don\'t '
            'preserve `trace--<exp>--*.csv` in S3, so V1 task durations '
            'cannot be recovered post-hoc. Showing V2 only._\n\n'
            + md_table(
                ['Sim', 'V2 wall (s)', 'V2 s/tick'],
                [[r[0], r[2], r[4]] for r in runtime_rows]))
    elif not v2_sim and v1_sim:
        runtime_table = (
            '_V2 trace CSV not available._\n\n'
            + md_table(
                ['Sim', 'V1 wall (s)', 'V1 s/tick'],
                [[r[0], r[1], r[3]] for r in runtime_rows]))
    elif not v1_sim and not v2_sim:
        runtime_table = '_No trace CSVs available for either run._\n'
    else:
        runtime_table = md_table(
            ['Sim', 'V1 wall (s)', 'V2 wall (s)',
             'V1 s/tick', 'V2 s/tick', 'Δ wall %'],
            runtime_rows)

    # Analysis plots
    plot_blocks = []
    for seed in seeds:
        for gen in gens:
            v1_p = find_plot(
                args.v1_id, 'mass_fraction_summary', seed, gen)
            v2_p = find_plot(
                args.v2_id, 'mass_fraction_summary', seed, gen)
            v1_r = stage_asset(v1_p, assets_dir, assets_rel,
                               f'mass_fraction_summary__seed{seed}_gen{gen}_v1')
            v2_r = stage_asset(v2_p, assets_dir, assets_rel,
                               f'mass_fraction_summary__seed{seed}_gen{gen}_v2')
            plot_blocks.append(plot_row(
                f'mass_fraction_summary — seed {seed}, gen {gen}',
                v1_r, v2_r, v1_p, v2_p))
    for kind in ['protein_counts_validation',
                 'subgenerational_expression_table',
                 'ecocyc_table']:
        v1_p = find_plot(args.v1_id, kind)
        v2_p = find_plot(args.v2_id, kind)
        v1_r = stage_asset(v1_p, assets_dir, assets_rel, f'{kind}_v1')
        v2_r = stage_asset(v2_p, assets_dir, assets_rel, f'{kind}_v2')
        plot_blocks.append(plot_row(
            f'{kind} (multiseed)', v1_r, v2_r, v1_p, v2_p))

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
