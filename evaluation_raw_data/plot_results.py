#!/usr/bin/env python3
"""Load and plot CFL solver evaluation results from full_combined.json.

Data layout (see full_combined.json):

    { algorithm: { benchmark: { test_case: [time_ms, mem_mb, reachable_pairs] } } }

  - algorithm: solver + grammar combination, e.g. "adv-mtx-PEG"
  - benchmark: program name, e.g. "povray"
  - test_case: module name, e.g. "octree.bc"
  - record[0]: time of one iteration of the inner iterative processing
               (currently unused)
  - record[1]: total file-processing time in milliseconds
  - record[2]: peak memory consumption in MB

Grammars: PEG = copy-grammar, PAG = mem-grammar. Within each grammar the
compared solvers are: baseline, pocr-hybrid, pocr-alias, mtx, adv-mtx.

For each grammar this script writes six figures (geometric means over
modules per program, no filters):

    time_<g>.png          grouped bars: geometric-mean runtime per benchmark,
                          with min-max whiskers over the individual files
    time_total_<g>.png    grouped bars: total runtime per benchmark (sum over
                          all files)
    memory_<g>.png        grouped bars: geometric-mean peak memory per
                          benchmark
    speedup_<g>.png       grouped bars: speedup vs baseline per benchmark
                          (geometric-mean based; always log y-axis, reference
                          line at 1.0)
    speedup_total_<g>.png grouped bars: speedup vs baseline per benchmark
                          (total-time based; always log y-axis, reference line
                          at 1.0)
    memory_ratio_<g>.png  grouped bars: memory improvement vs baseline per
                          benchmark (always log y-axis, reference line at 1.0;
                          > 1 means the solver uses less memory)

Benchmarks with no recorded cases in any algorithm (e.g. cactuBSSN_r)
are filtered out by default; use --keep-empty to retain them.

Axis scales: the time charts are log-scaled by default (per-file times
span ~5 decades); use --linear-y for linear. The memory chart is linear
by default; use --log-y for log. The ratio charts are always log-scaled.

Usage:
    .venv/bin/python plot_results.py [--data PATH] [--out-dir DIR]
                                     [--log-y | --linear-y]
                                     [--grammars PEG PAG] [--keep-empty]
"""

import argparse
import json
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb

GRAMMARS = ("PEG", "PAG")
GRAMMAR_LABEL = {"PEG": "copy-grammar (PEG)", "PAG": "mem-grammar (PAG)"}
SOLVER_ORDER = ["baseline", "pocr-hybrid", "pocr-alias", "mtx", "adv-mtx"]
SOLVER_COLORS = {
    "baseline": "#8c8c8c",
    "pocr-hybrid": "#1f77b4",
    "pocr-alias": "#ff7f0e",
    "mtx": "#2ca02c",
    "adv-mtx": "#d62728",
}


def load_data(path):
    with open(path) as f:
        return json.load(f)


def geomean(values):
    """Geometric mean, ignoring non-positive values. 0.0 if none remain."""
    vals = [v for v in values if v > 0]
    if not vals:
        return 0.0
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def solvers_for_grammar(data, grammar):
    """Map display name -> algorithm key for one grammar.

    The bare grammar key (e.g. "PEG") is the baseline; keys of the form
    "<solver>-<grammar>" map to their solver name.
    """
    out = {}
    for algo in data:
        if algo == grammar:
            out["baseline"] = algo
        elif algo.endswith("-" + grammar):
            out[algo[: -(len(grammar) + 1)]] = algo
    return out


def aggregate(data, grammar, skip_empty=True):
    """Compute per-(benchmark, solver) geometric means.

    Returns (agg, benches) where
        agg[benchmark][solver] = {"time": gm_ms, "mem": gm_mb}
    Missing (algorithm, benchmark, case) entries are tolerated: each
    solver's geomean is taken over the cases it actually has.
    When skip_empty is true, benchmarks with no cases in any algorithm
    are excluded from benches.
    """
    solvers = solvers_for_grammar(data, grammar)
    if "baseline" not in solvers:
        raise KeyError(f"baseline algorithm {grammar!r} not found in data")
    benches = [
        b
        for b in data[solvers["baseline"]].keys()
        if not skip_empty or any(data[a].get(b) for a in solvers.values())
    ]
    agg = {}
    for b in benches:
        agg[b] = {}
        for name, key in solvers.items():
            cases = data[key].get(b, {})
            times = [v[1] for v in cases.values()]
            agg[b][name] = {
                "time": geomean(times),
                "time_min": min(times) if times else 0.0,
                "time_max": max(times) if times else 0.0,
                "time_sum": sum(times),
                "mem": geomean(v[2] for v in cases.values()),
            }
    return agg, benches


def _darken(color, factor=0.7):
    """Return `color` scaled toward black by `factor` (for whiskers)."""
    r, g, b = to_rgb(color)
    return (r * factor, g * factor, b * factor)


def _style_axes(ax, benches, ylabel, title, log_y, center_offset=0.0):
    ax.set_xticks([xi + center_offset for xi in range(len(benches))])
    ax.set_xticklabels(benches, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(axis="y", alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    if log_y:
        ax.set_yscale("log")


def plot_bars(agg, benches, metric, filename, ylabel, title, out_dir, log_y,
              show_range=False):
    """Grouped bar chart of one metric per benchmark, per solver.

    When show_range is true, min-max whiskers over the underlying cases
    (keys "<metric>_min" / "<metric>_max") are drawn on top of the bars.
    On a log scale a non-positive min cannot be plotted, so the lower
    whisker is skipped for such points.
    """
    fig, ax = plt.subplots(figsize=(14, 6))
    n = len(SOLVER_ORDER)
    width = 0.8 / n
    for i, name in enumerate(SOLVER_ORDER):
        vals = [agg[b].get(name, {}).get(metric, 0.0) for b in benches]
        xs = [xi + i * width for xi in range(len(benches))]
        ax.bar(xs, vals, width, label=name, color=SOLVER_COLORS[name])
        if show_range:
            lower, upper = [], []
            for b in benches:
                d = agg[b].get(name, {})
                gm = d.get(metric, 0.0)
                lo = d.get(f"{metric}_min", gm)
                hi = d.get(f"{metric}_max", gm)
                lower.append(gm - lo if gm > 0 and lo > 0 else float("nan"))
                upper.append(hi - gm if gm > 0 and hi >= gm else float("nan"))
            ax.errorbar(
                xs,
                vals,
                yerr=[lower, upper],
                fmt="none",
                ecolor=_darken(SOLVER_COLORS[name]),
                elinewidth=0.8,
                capsize=2,
                capthick=0.8,
            )
    _style_axes(ax, benches, ylabel, title, log_y, (n - 1) * width / 2)
    path = os.path.join(out_dir, filename)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_ratio(agg, benches, grammar, metric, filename, ylabel, title_prefix, out_dir):
    """Grouped bar chart of baseline/solver ratio for `metric` per benchmark.

    The ratio is computed from the per-benchmark geometric means
    (baseline geomean / solver geomean), so zero-valued cases cannot cause
    division by zero; such benchmarks are simply skipped (NaN bar).
    Always drawn with a log y-axis and a reference line at 1.0.
    """
    fig, ax = plt.subplots(figsize=(14, 6))
    others = [s for s in SOLVER_ORDER if s != "baseline"]
    width = 0.8 / len(others)
    for i, name in enumerate(others):
        vals = []
        for b in benches:
            base_v = agg[b].get("baseline", {}).get(metric, 0.0)
            sol_v = agg[b].get(name, {}).get(metric, 0.0)
            vals.append(base_v / sol_v if base_v > 0 and sol_v > 0 else float("nan"))
        ax.bar(
            [xi + i * width for xi in range(len(benches))],
            vals,
            width,
            label=name,
            color=SOLVER_COLORS[name],
        )
    ax.axhline(1.0, color="k", linestyle="--", linewidth=0.8)
    _style_axes(
        ax,
        benches,
        ylabel,
        f"{title_prefix} — {GRAMMAR_LABEL[grammar]}",
        log_y=True,
        center_offset=(len(others) - 1) * width / 2,
    )
    path = os.path.join(out_dir, filename)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def summarize(agg, benches, grammar):
    """Print overall (cross-benchmark) geomean ratios vs baseline per solver."""
    print(f"\n{GRAMMAR_LABEL[grammar]} — overall geomean vs baseline:")
    print(f"  {'solver':12s} {'speedup(mean)':>14s} {'speedup(total)':>15s} {'mem gain':>10s}")
    for name in SOLVER_ORDER:
        if name == "baseline":
            continue
        cells = []
        for metric in ("time", "time_sum", "mem"):
            ratios = []
            for b in benches:
                base_v = agg[b].get("baseline", {}).get(metric, 0.0)
                sol_v = agg[b].get(name, {}).get(metric, 0.0)
                if base_v > 0 and sol_v > 0:
                    ratios.append(base_v / sol_v)
            cells.append(f"{geomean(ratios):9.2f}x")
        print(f"  {name:12s} {cells[0]:>14s} {cells[1]:>15s} {cells[2]:>10s}")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(
        description="Plot CFL solver evaluation results from full_combined.json."
    )
    ap.add_argument(
        "--data",
        default=os.path.join(here, "full_combined.json"),
        help="path to full_combined.json (default: %(default)s)",
    )
    ap.add_argument(
        "--out-dir",
        default=os.path.join(here, "plots"),
        help="directory for output PNGs (default: %(default)s)",
    )
    scale = ap.add_mutually_exclusive_group()
    scale.add_argument(
        "--log-y",
        action="store_true",
        help="use log scale for the memory bar chart as well "
        "(time charts are log-scaled by default; ratio charts always are)",
    )
    scale.add_argument(
        "--linear-y",
        action="store_true",
        help="use linear scale for the time bar charts (log by default)",
    )
    ap.add_argument(
        "--grammars",
        nargs="+",
        choices=list(GRAMMARS),
        default=list(GRAMMARS),
        help="grammars to plot (default: both)",
    )
    ap.add_argument(
        "--keep-empty",
        action="store_true",
        help="keep benchmarks with no recorded cases (default: filter them out)",
    )
    args = ap.parse_args()

    data = load_data(args.data)
    os.makedirs(args.out_dir, exist_ok=True)

    for g in args.grammars:
        agg, benches = aggregate(data, g, skip_empty=not args.keep_empty)
        label = GRAMMAR_LABEL[g]
        paths = [
            plot_bars(
                agg,
                benches,
                "time",
                f"time_{g}.png",
                "Time (ms)",
                f"Geometric-mean runtime per benchmark (min-max over files) — {label}",
                args.out_dir,
                not args.linear_y,
                show_range=True,
            ),
            plot_bars(
                agg,
                benches,
                "time_sum",
                f"time_total_{g}.png",
                "Total time (ms)",
                f"Total runtime per benchmark (sum over files) — {label}",
                args.out_dir,
                not args.linear_y,
            ),
            plot_bars(
                agg,
                benches,
                "mem",
                f"memory_{g}.png",
                "Peak memory (MB)",
                f"Geometric-mean peak memory per benchmark — {label}",
                args.out_dir,
                args.log_y,
            ),
            plot_ratio(
                agg,
                benches,
                g,
                "time",
                f"speedup_{g}.png",
                "Speedup vs baseline (x)",
                "Speedup vs baseline per benchmark (geometric mean)",
                args.out_dir,
            ),
            plot_ratio(
                agg,
                benches,
                g,
                "time_sum",
                f"speedup_total_{g}.png",
                "Speedup vs baseline (x)",
                "Speedup vs baseline per benchmark (total time)",
                args.out_dir,
            ),
            plot_ratio(
                agg,
                benches,
                g,
                "mem",
                f"memory_ratio_{g}.png",
                "Memory improvement vs baseline (x)",
                "Memory improvement vs baseline per benchmark",
                args.out_dir,
            ),
        ]
        for p in paths:
            print(f"wrote {p}")
        summarize(agg, benches, g)


if __name__ == "__main__":
    main()
