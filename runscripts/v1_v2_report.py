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


def division_times(experiment_id):
    """{seed: {gen: division_time_seconds}}"""
    out = {}
    workdir_root = f'{REPO_ROOT}/out/{experiment_id}/nextflow/nextflow_workdirs'
    for sh_path in glob.glob(f'{workdir_root}/*/*/.command.sh'):
        if 'ecoli_master_sim.py' not in open(sh_path).read():
            continue
        sh = open(sh_path).read()
        seed_m = re.search(r'--lineage_seed\s+(\S+)', sh)
        # ``--daughter_outdir`` points at the *current* sim's output
        # dir; ``--initial_state_file`` points at the parent gen's
        # state — using initial_state_file misclassifies every gen_2
        # sim as gen_1.
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
        path = (f'{base}/variant=0/lineage_seed={seed}/generation={gen}/'
                f'agent_id={"00" if gen == 2 else "0"}/plots/analysis={kind}')
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

    v1_sim = per_sim_dict(v1_trace)
    v2_sim = per_sim_dict(v2_trace)

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

    md = (
        f'# vEcoli v1 vs v2 — {args.v1_id} vs {args.v2_id}\n\n'
        '_Generated from latest workflow runs by `runscripts/v1_v2_report.py`._\n\n'
        + (parity_md + '\n' if parity_md else '')
        + '## Cell cycle / division times\n\n'
        f'{div_table}\n\n'
        '## Runtime per task (sum across instances)\n\n'
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
