"""Generate an HTML report comparing v1 vs v2 workflows.

Pulls task durations from each workflow's nextflow trace CSV and
embeds the analysis plot HTMLs side-by-side for each (seed, generation)
combination.

By default, writes a portable report at ``doc/_static/v1_v2_report.html``
plus a sibling ``v1_v2_report_assets/`` directory of copied plot HTMLs
referenced via relative iframes, so the bundle can be committed and
viewed on GitHub (via htmlpreview.github.io) or rendered locally from
the checkout.
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
        # ``--daughter_outdir`` always points at the *current* sim's
        # output dir; ``--initial_state_file`` points at the parent
        # gen's state — using initial_state_file misclassifies every
        # gen_2 sim as gen_1.
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
    """Extract the first Vega-Lite spec from a plot HTML, render it
    to PNG via vl-convert, and write it to out_png_path. Returns True
    on success, False if no spec was found."""
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
    """Stage the plot for the report and return its relative path.

    For Vega-Lite HTMLs, render to PNG and return the PNG path.
    For TSVs, copy as-is. Returns None if src_path is None."""
    if src_path is None:
        return None
    if src_path.endswith('.html'):
        png_name = f'{stem}.png'
        png_path = os.path.join(assets_dir, png_name)
        if vegalite_to_png(src_path, png_path):
            return f'{assets_rel}/{png_name}'
        # Fallback: plain HTML copy + iframe
        html_name = f'{stem}.html'
        shutil.copyfile(src_path, os.path.join(assets_dir, html_name))
        return f'{assets_rel}/{html_name}'
    ext = os.path.splitext(src_path)[1]
    dest_name = f'{stem}{ext}'
    shutil.copyfile(src_path, os.path.join(assets_dir, dest_name))
    return f'{assets_rel}/{dest_name}'


def embed_plot(label, v1_rel, v2_rel, v1_abs, v2_abs):
    def block(rel, abs_path):
        if rel is None:
            return '<em>(missing)</em>'
        if rel.endswith('.png'):
            return (f'<img src="{rel}" style="max-width:100%;'
                    f'border:1px solid #ccc">')
        if rel.endswith('.html'):
            return (f'<iframe src="{rel}" width="100%" '
                    f'height="500" style="border:1px solid #ccc"></iframe>')
        with open(abs_path) as f:
            preview = '\n'.join(f.readlines()[:20])
        return f'<pre style="max-height:300px;overflow:auto;background:#f8f8f8;padding:8px">{preview}</pre>'
    return f'''
    <h3>{label}</h3>
    <div style="display:flex;gap:12px">
        <div style="flex:1"><h4>V1</h4>{block(v1_rel, v1_abs)}</div>
        <div style="flex:1"><h4>V2</h4>{block(v2_rel, v2_abs)}</div>
    </div>
    '''


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', default='doc/_static/v1_v2_report.html')
    args = p.parse_args()

    out_path = os.path.abspath(args.out)
    out_dir = os.path.dirname(out_path)
    out_stem = os.path.splitext(os.path.basename(out_path))[0]
    assets_rel = f'{out_stem}_assets'
    assets_dir = os.path.join(out_dir, assets_rel)
    if os.path.isdir(assets_dir):
        shutil.rmtree(assets_dir)
    os.makedirs(assets_dir, exist_ok=True)

    v1_trace = load_trace('two_generations_v1')
    v2_trace = load_trace('two_generations_v2')

    v1_dt = division_times('two_generations_v1')
    v2_dt = division_times('two_generations_v2')

    rows = []
    rows.append('<table border="1" cellpadding="6" style="border-collapse:collapse">')
    rows.append('<tr><th>Seed</th><th>Gen</th>'
                '<th>V1 div_time</th><th>V2 div_time</th>'
                '<th>V1 cycle</th><th>V2 cycle</th><th>Δ%</th></tr>')
    for seed in sorted(set(v1_dt) | set(v2_dt)):
        prev_v1 = 0.0
        prev_v2 = 0.0
        for gen in sorted(set(v1_dt.get(seed, {})) | set(v2_dt.get(seed, {}))):
            v1d = v1_dt.get(seed, {}).get(gen)
            v2d = v2_dt.get(seed, {}).get(gen)
            v1_cycle = (v1d - prev_v1) if v1d else None
            v2_cycle = (v2d - prev_v2) if v2d else None
            delta_pct = ((v2_cycle - v1_cycle) / v1_cycle * 100) \
                if v1_cycle and v2_cycle else None
            rows.append(
                f'<tr><td>{seed}</td><td>{gen}</td>'
                f'<td>{v1d:.0f}</td><td>{v2d:.0f}</td>'
                f'<td>{v1_cycle:.0f}</td><td>{v2_cycle:.0f}</td>'
                f'<td>{delta_pct:+.1f}%</td></tr>')
            if v1d:
                prev_v1 = v1d
            if v2d:
                prev_v2 = v2d
    rows.append('</table>')
    div_table = '\n'.join(rows)

    # Per-sim runtime table, indexed by (seed, gen) — apples-to-apples
    # since both runs share the same simulated work for each cell.
    runtime_rows = ['<table border="1" cellpadding="6" '
                    'style="border-collapse:collapse">']
    runtime_rows.append(
        '<tr><th>Sim</th><th>V1 wall (s)</th><th>V2 wall (s)</th>'
        '<th>V1 s/tick</th><th>V2 s/tick</th><th>Δ wall %</th></tr>')

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
            v1_per_s = f'{v1_per:.3f}' if v1_per else '-'
            v2_per_s = f'{v2_per:.3f}' if v2_per else '-'
            delta_s = f'{delta:+.1f}%' if delta is not None else '-'
            v1_w_s = f'{v1_wall:.0f}' if v1_wall else '-'
            v2_w_s = f'{v2_wall:.0f}' if v2_wall else '-'
            runtime_rows.append(
                f'<tr><td>seed {seed} gen {gen}</td>'
                f'<td>{v1_w_s}</td><td>{v2_w_s}</td>'
                f'<td>{v1_per_s}</td><td>{v2_per_s}</td>'
                f'<td>{delta_s}</td></tr>')
            if v1_wall:
                v1_sim_total += v1_wall
                prev_v1 = v1_div or prev_v1
            if v2_wall:
                v2_sim_total += v2_wall
                prev_v2 = v2_div or prev_v2
    if v1_sim_total and v2_sim_total:
        runtime_rows.append(
            f'<tr><th>SIM TOTAL</th>'
            f'<th>{v1_sim_total:.0f}</th><th>{v2_sim_total:.0f}</th>'
            f'<th>-</th><th>-</th>'
            f'<th>{(v2_sim_total-v1_sim_total)/v1_sim_total*100:+.1f}%</th></tr>')
    runtime_rows.append('</table>')
    runtime_table = '\n'.join(runtime_rows)

    plots = []
    # Per (seed, gen) mass_fraction_summary
    for seed in ['0', '1']:
        for gen in [1, 2]:
            v1_p = find_plot('two_generations_v1', 'mass_fraction_summary', seed, gen)
            v2_p = find_plot('two_generations_v2', 'mass_fraction_summary', seed, gen)
            stem1 = f'mass_fraction_summary__seed{seed}_gen{gen}_v1'
            stem2 = f'mass_fraction_summary__seed{seed}_gen{gen}_v2'
            v1_r = stage_asset(v1_p, assets_dir, assets_rel, stem1)
            v2_r = stage_asset(v2_p, assets_dir, assets_rel, stem2)
            plots.append(embed_plot(
                f'mass_fraction_summary — seed {seed}, gen {gen}',
                v1_r, v2_r, v1_p, v2_p))
    # Multiseed
    for kind in ['protein_counts_validation', 'subgenerational_expression_table',
                 'ecocyc_table']:
        v1_p = find_plot('two_generations_v1', kind)
        v2_p = find_plot('two_generations_v2', kind)
        v1_r = stage_asset(v1_p, assets_dir, assets_rel, f'{kind}_v1')
        v2_r = stage_asset(v2_p, assets_dir, assets_rel, f'{kind}_v2')
        plots.append(embed_plot(
            f'{kind} (multiseed)', v1_r, v2_r, v1_p, v2_p))

    html = f'''<!DOCTYPE html>
<html><head><title>vEcoli v1 vs v2 — two_generations</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: 30px auto; padding: 0 20px; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 6px; }}
h2 {{ margin-top: 36px; color: #444; }}
th {{ background: #eee; }}
td {{ text-align: right; }}
td:first-child, th:first-child {{ text-align: left; }}
</style>
</head>
<body>
<h1>vEcoli v1 vs v2 — two_generations comparison</h1>
<p>Generated from latest workflow runs.</p>
<h2>Cell cycle / division times</h2>
{div_table}
<h2>Runtime per task (sum across instances)</h2>
{runtime_table}
<h2>Analysis plots</h2>
{''.join(plots)}
</body></html>
'''
    with open(out_path, 'w') as f:
        f.write(html)
    print(f'Report written: {out_path}')
    print(f'Assets dir:    {assets_dir}')


if __name__ == '__main__':
    main()
