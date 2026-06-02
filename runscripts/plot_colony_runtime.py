"""Plot wall-time vs sim-time for a Ray colony run.

Parses the heartbeat lines that ``runscripts/run_colony_ray.py`` writes
to its driver workflow log (once per ``HEARTBEAT_SECS`` of wall clock):

    [ray-colony] heartbeat: sim_time=<S>s  cells=<N>  wall=<W>s

Computes the instantaneous wall-time-per-sim-second between adjacent
heartbeats and renders a two-panel figure:

  - top:    sim seconds per wall second (instantaneous rate)
  - bottom: cell count over sim time (step plot)

Vertical lines mark division events parsed from
``DIVIDE! MOTHER <id> -> DAUGHTERS [<ids>]`` lines emitted by the
composite divide handler.

Usage:
    .venv/bin/python runscripts/plot_colony_runtime.py \\
        --log path/to/workflow.log -o out/colony_runtime.png

Or pipe a log over stdin:
    ssh head 'cat ~/foo_workflow.log' \\
        | .venv/bin/python runscripts/plot_colony_runtime.py --stdin \\
              -o out/colony.png

Invoked indirectly via ``runscripts/aws/vecoli_aws.sh run timing <alias>``,
which fetches the head's log and runs this script locally so the PNG
lands next to the rest of the comparison artifacts in ``out/``.
"""
import argparse
import re
import sys

import matplotlib
matplotlib.use('Agg')  # headless render — no display needed
import matplotlib.pyplot as plt


# Match BOTH heartbeat formats:
#   legacy run_colony_ray.py:   ``[ray-colony] heartbeat: sim_time=N.Ns cells=N wall=Ns``
#   cell-as-Composite driver:   ``[colony-cac] heartbeat: sim_t=N.Ns cells=N wall=Ns``
# Driver prefix is permissive so future variants don't need another fix
# here; key/value pair separators match either ``=`` form.
HB_RE = re.compile(
    r'\[(?:ray-colony|colony-cac)\] heartbeat:\s*'
    r'sim_(?:time|t)=([\d.]+)s\s+'
    r'cells=(\d+)\s+wall=([\d.]+)s'
)
DIV_RE = re.compile(
    r'DIVIDE!\s*MOTHER\s+(\S+)\s+->\s+DAUGHTERS\s+\[([^\]]+)\]'
)
EXP_RE = re.compile(r"experiment_id=['\"]?([^'\"\s]+)")


def parse_log(stream):
    rows = []        # (sim_time, cells, wall_time)
    divisions = []   # sim_time guess: best-effort, use prev heartbeat
    last_sim_t = None
    exp_id = None
    for line in stream:
        if exp_id is None:
            m = EXP_RE.search(line)
            if m:
                exp_id = m.group(1)
        m = HB_RE.search(line)
        if m:
            sim_t = float(m.group(1))
            rows.append((sim_t, int(m.group(2)), float(m.group(3))))
            last_sim_t = sim_t
            continue
        d = DIV_RE.search(line)
        if d:
            # Attribute division to the most recent heartbeat's sim_time
            # — actual divide happens between heartbeats, so this is a
            # lower bound for the marker (precise enough at 60s cadence).
            divisions.append((last_sim_t, d.group(1), d.group(2)))
    return rows, divisions, exp_id


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.split('\n\n', 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--log', help='path to workflow.log')
    ap.add_argument('--stdin', action='store_true',
                    help='read log from stdin instead of --log')
    ap.add_argument('-o', '--output', default='out/colony_runtime.png',
                    help='output PNG path (default: %(default)s)')
    ap.add_argument('--title', default=None,
                    help='plot title (default: derived from experiment_id)')
    args = ap.parse_args()

    if args.stdin:
        rows, divisions, exp_id = parse_log(sys.stdin)
    elif args.log:
        with open(args.log) as f:
            rows, divisions, exp_id = parse_log(f)
    else:
        ap.error('--log or --stdin required')

    if not rows:
        print('plot_colony_runtime: no heartbeat lines found in log.',
              file=sys.stderr)
        return 1

    sim_t = [r[0] for r in rows]
    cells = [r[1] for r in rows]
    wall_t = [r[2] for r in rows]

    # Instantaneous rate: sim seconds advanced per wall second between
    # adjacent heartbeats. Plotted at the midpoint of each interval.
    rate_x, rate_y = [], []
    for i in range(1, len(rows)):
        dt_sim = sim_t[i] - sim_t[i - 1]
        dt_wall = wall_t[i] - wall_t[i - 1]
        if dt_wall > 0:
            rate_x.append(0.5 * (sim_t[i] + sim_t[i - 1]))
            rate_y.append(dt_sim / dt_wall)

    title = args.title or (
        f'Colony runtime — {exp_id}' if exp_id else 'Colony runtime')

    fig, (ax_rate, ax_cells) = plt.subplots(
        2, 1, figsize=(10, 6), sharex=True,
        gridspec_kw={'height_ratios': [2, 1]})
    fig.suptitle(title)

    if rate_y:
        ax_rate.plot(rate_x, rate_y, marker='o', color='C0',
                     label='sim/wall rate')
    ax_rate.set_ylabel('sim sec / wall sec')
    ax_rate.set_ylim(bottom=0)
    ax_rate.grid(True, alpha=0.3)

    ax_cells.step(sim_t, cells, where='post', color='C2', marker='o')
    ax_cells.set_ylabel('cells')
    ax_cells.set_xlabel('sim time (s)')
    ax_cells.set_ylim(bottom=0)
    ax_cells.grid(True, alpha=0.3)

    for sim_at_div, mother, daughters in divisions:
        if sim_at_div is None:
            continue
        ax_rate.axvline(sim_at_div, color='C3', alpha=0.4, linestyle='--')
        ax_cells.axvline(sim_at_div, color='C3', alpha=0.4, linestyle='--')

    # Small summary annotation
    if rate_y:
        first = rate_y[0]
        last = rate_y[-1]
        drop_pct = (1 - last / first) * 100 if first > 0 else 0
        ax_rate.text(
            0.99, 0.97,
            f'first={first:.2f}  last={last:.2f}  Δ={drop_pct:+.0f}%',
            transform=ax_rate.transAxes,
            ha='right', va='top',
            fontsize=9,
            bbox={'facecolor': 'white', 'alpha': 0.8, 'edgecolor': 'gray'},
        )

    plt.tight_layout()
    plt.savefig(args.output, dpi=120)
    print(f'plot_colony_runtime: wrote {args.output}', file=sys.stderr)
    print(f'  heartbeats={len(rows)}  divisions={len(divisions)}',
          file=sys.stderr)
    if rate_y:
        print(f'  rate first={rate_y[0]:.2f}  last={rate_y[-1]:.2f}  '
              f'(drop {(1 - rate_y[-1] / rate_y[0]) * 100:.0f}% over '
              f'sim_time={sim_t[-1] - sim_t[0]:.0f}s)',
              file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
