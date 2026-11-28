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
  - record[2]: peak memory consumption in KB (plotted in GB)

Grammars: PEG = copy-grammar, PAG = mem-grammar. Within each grammar the
compared solvers are: baseline, pocr-hybrid, pocr-alias, mtx, adv-mtx.

For each grammar this script writes eight figures (geometric means over
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
    violin_time_<g>.png   grouped violins: distribution of per-file runtimes
                          per benchmark (KDE in log space by default, so the
                          shape reflects the heavy-tailed data; --linear-y
                          switches to a plain linear violin)
    violin_memory_<g>.png grouped violins: distribution of per-file peak
                          memory per benchmark (linear by default, like the
                          memory bar chart; --log-y switches to a log-space
                          KDE)

Two condensed series with the same eight figures are also available, selected
via --series (any combination; default: all):

  reduced  -> <out-dir>/reduced/: baseline, best pocr-based variant, best
           mtx-based variant. Per benchmark, the winner of each family is the
           variant with the lowest value of the quantity that figure plots
           (e.g. lowest geomean time for the time/speedup figures, lowest
           geomean memory for the memory figures).
  mixed    -> <out-dir>/mixed/: baseline, best pocr-based variant, and both
           mtx variants (mtx, adv-mtx) — the pocr family is collapsed to its
           per-benchmark best while the mtx family is shown in full.

Benchmarks with no recorded cases in any algorithm (e.g. cactuBSSN_r)
are filtered out by default; use --keep-empty to retain them.

Axis scales: the time charts are log-scaled by default (per-file times
span ~5 decades); use --linear-y for linear. The memory chart is linear
by default; use --log-y for log. The ratio charts are always log-scaled.
Time is displayed in seconds (the raw records are in milliseconds).

Usage:
    .venv/bin/python plot_results.py [--data PATH] [--out-dir DIR]
                                     [--log-y | --linear-y]
                                     [--grammars PEG PAG] [--keep-empty]
                                     [--series {full,reduced,mixed} ...]
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
# Reduced "best-of" series: baseline plus the best pocr-based and best
# mtx-based variant per benchmark (see resolve_best).
REDUCED_SOLVERS = ["baseline", "best-pocr", "best-mtx"]
REDUCED_COLORS = {
    "baseline": "#8c8c8c",
    "best-pocr": "#1f77b4",
    "best-mtx": "#d62728",
}
# Mixed series: baseline, best pocr-based, and both mtx variants (see
# resolve_best_pocr).
MIXED_SOLVERS = ["baseline", "best-pocr", "mtx", "adv-mtx"]
MIXED_COLORS = {
    "baseline": "#8c8c8c",
    "best-pocr": "#1f77b4",
    "mtx": "#2ca02c",
    "adv-mtx": "#d62728",
}
POCR_VARIANTS = ["pocr-hybrid", "pocr-alias"]
MTX_VARIANTS = ["mtx", "adv-mtx"]


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
        agg[benchmark][solver] = {"time": gm_s, "mem": gm_gb}
    Times are stored in seconds (the raw records are in milliseconds);
    memory in GB. Missing (algorithm, benchmark, case) entries are
    tolerated: each solver's geomean is taken over the cases it actually
    has. When skip_empty is true, benchmarks with no cases in any
    algorithm are excluded from benches.
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
            # raw times are in milliseconds; store in seconds
            times = [v[1] / 1000.0 for v in cases.values()]
            agg[b][name] = {
                "time": geomean(times),
                "time_min": min(times) if times else 0.0,
                "time_max": max(times) if times else 0.0,
                "time_sum": sum(times),
                # raw peak memory is in KB; store in GB (geomean is homogeneous,
                # so converting after averaging is exact)
                "mem": geomean(v[2] for v in cases.values()) / 1024.0 / 1024.0,
            }
    return agg, benches


def collect_values(data, grammar, field, scale, skip_empty=True):
    """Collect raw per-file values of one record field.

    Returns (raw, benches) where
        raw[benchmark][solver] = [per-file record[field] * scale]
    for the five base solvers. Unlike aggregate(), this keeps the full
    per-file lists so distributions (e.g. violin plots) can be drawn.
    """
    solvers = solvers_for_grammar(data, grammar)
    if "baseline" not in solvers:
        raise KeyError(f"baseline algorithm {grammar!r} not found in data")
    benches = [
        b
        for b in data[solvers["baseline"]].keys()
        if not skip_empty or any(data[a].get(b) for a in solvers.values())
    ]
    raw = {}
    for b in benches:
        raw[b] = {}
        for name, key in solvers.items():
            cases = data[key].get(b, {})
            raw[b][name] = [v[field] * scale for v in cases.values()]
    return raw, benches


def resolve_violin(raw, benches, series_solvers):
    """Resolve a series' per-solver per-benchmark value lists for a violin.

    Direct solvers pass through; "best-pocr"/"best-mtx" resolve per
    benchmark to the winning variant's raw values (lowest geomean).
    """
    out = {}
    for name in series_solvers:
        out[name] = {}
        for b in benches:
            if name in ("best-pocr", "best-mtx"):
                family = POCR_VARIANTS if name == "best-pocr" else MTX_VARIANTS
                best = None
                for v in family:
                    gm = geomean(raw[b][v])
                    if gm > 0 and (best is None or gm < best[0]):
                        best = (gm, raw[b][v])
                out[name][b] = best[1] if best else []
            else:
                out[name][b] = raw[b][name]
    return out


def _pick_best(block, variants, selector):
    """Return the stat block of the variant with the lowest selector value.

    Lower is better. Variants whose selector value is non-positive (no
    data) are ignored; if none is usable the first variant's block is
    returned (renders as an empty bar).
    """
    scored = [(k, selector(block[k])) for k in variants]
    scored = [(k, v) for k, v in scored if v > 0]
    if not scored:
        return block[variants[0]]
    return block[min(scored, key=lambda kv: kv[1])[0]]


def resolve_best(agg, benches, selector):
    """Build the reduced best-of agg for a given selection criterion.

    For each benchmark, "best-pocr" is the pocr variant and "best-mtx" the
    mtx variant with the lowest `selector(block)` value. The winning
    variant's full stat block is carried over so min-max whiskers and
    totals stay coherent.
    """
    return {
        b: {
            "baseline": agg[b]["baseline"],
            "best-pocr": _pick_best(agg[b], POCR_VARIANTS, selector),
            "best-mtx": _pick_best(agg[b], MTX_VARIANTS, selector),
        }
        for b in benches
    }


def resolve_best_pocr(agg, benches, selector):
    """Build the mixed agg: best pocr variant plus both mtx variants.

    Like resolve_best but only the pocr family is collapsed to its per-
    benchmark best; "mtx" and "adv-mtx" pass through unchanged.
    """
    return {
        b: {
            "baseline": agg[b]["baseline"],
            "best-pocr": _pick_best(agg[b], POCR_VARIANTS, selector),
            "mtx": agg[b]["mtx"],
            "adv-mtx": agg[b]["adv-mtx"],
        }
        for b in benches
    }


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
              show_range=False, solvers=None, colors=None):
    """Grouped bar chart of one metric per benchmark, per solver.

    When show_range is true, min-max whiskers over the underlying cases
    (keys "<metric>_min" / "<metric>_max") are drawn on top of the bars.
    On a log scale a non-positive min cannot be plotted, so the lower
    whisker is skipped for such points.
    """
    solvers = SOLVER_ORDER if solvers is None else solvers
    colors = SOLVER_COLORS if colors is None else colors
    fig, ax = plt.subplots(figsize=(14, 6))
    n = len(solvers)
    width = 0.8 / n
    for i, name in enumerate(solvers):
        vals = [agg[b].get(name, {}).get(metric, 0.0) for b in benches]
        xs = [xi + i * width for xi in range(len(benches))]
        ax.bar(xs, vals, width, label=name, color=colors[name])
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
                ecolor=_darken(colors[name]),
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


def plot_ratio(agg, benches, grammar, metric, filename, ylabel, title_prefix,
               out_dir, solvers=None, colors=None):
    """Grouped bar chart of baseline/solver ratio for `metric` per benchmark.

    The ratio is computed from the per-benchmark geometric means
    (baseline geomean / solver geomean), so zero-valued cases cannot cause
    division by zero; such benchmarks are simply skipped (NaN bar).
    Always drawn with a log y-axis and a reference line at 1.0. The best
    (highest) bar of each group is labeled with its value.
    """
    solvers = SOLVER_ORDER if solvers is None else solvers
    colors = SOLVER_COLORS if colors is None else colors
    fig, ax = plt.subplots(figsize=(14, 6))
    others = [s for s in solvers if s != "baseline"]
    width = 0.8 / len(others)
    # ratios[xi][i] = ratio of solver i at benchmark xi (NaN if no data)
    ratios = [[float("nan")] * len(others) for _ in range(len(benches))]
    for i, name in enumerate(others):
        for xi, b in enumerate(benches):
            base_v = agg[b].get("baseline", {}).get(metric, 0.0)
            sol_v = agg[b].get(name, {}).get(metric, 0.0)
            if base_v > 0 and sol_v > 0:
                ratios[xi][i] = base_v / sol_v
        ax.bar(
            [xi + i * width for xi in range(len(benches))],
            [ratios[xi][i] for xi in range(len(benches))],
            width,
            label=name,
            color=colors[name],
        )
    # label the best (highest) bar of each group with its value
    for xi, row in enumerate(ratios):
        finite = [(v, i) for i, v in enumerate(row) if math.isfinite(v)]
        if not finite:
            continue
        v, i = max(finite)
        ax.text(
            xi + i * width,
            v,
            f"{v:.1f}",
            ha="center",
            va="bottom",
            fontsize=7,
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


def plot_violin(datasets_by_solver, benches, filename, title, out_dir, log,
                solvers=None, colors=None, ylabel="Time (s)"):
    """Grouped violin plot of per-file values, one violin per (bench, solver).

    When log is true the times are transformed to log10 before the KDE so the
    violin shape reflects the (heavy-tailed) distribution correctly; the y
    axis is then relabeled with the actual values. Empty datasets are skipped.
    """
    import matplotlib.ticker as ticker

    solvers = SOLVER_ORDER if solvers is None else solvers
    colors = SOLVER_COLORS if colors is None else colors
    fig, ax = plt.subplots(figsize=(14, 6))
    n = len(solvers)
    width = 0.8 / n
    for i, name in enumerate(solvers):
        positions, datasets = [], []
        for xi, b in enumerate(range(len(benches))):
            ds = datasets_by_solver[name][benches[b]]
            if log:
                ds = [math.log10(t) for t in ds if t > 0]
            # the KDE needs at least two points; skip anything smaller
            if len(ds) < 2:
                continue
            positions.append(xi + i * width)
            datasets.append(ds)
        if not datasets:
            continue
        parts = ax.violinplot(
            datasets,
            positions=positions,
            widths=width * 0.9,
            showmedians=True,
            showextrema=False,
        )
        for body in parts["bodies"]:
            body.set_facecolor(colors[name])
            body.set_alpha(0.55)
            body.set_edgecolor(_darken(colors[name]))
        # median lines: a list of Line2D in matplotlib < 3.11, a single
        # LineCollection in >= 3.11
        meds = parts.get("cmedians", parts.get("medians", []))
        if isinstance(meds, list):
            for med in meds:
                med.set_color("black")
                med.set_linewidth(1.0)
        else:
            meds.set_color("black")
            meds.set_linewidth(1.0)
    _style_axes(ax, benches, ylabel, title, log_y=False,
                center_offset=(n - 1) * width / 2)
    # violinplot bodies carry no label, so build the legend manually
    # (after _style_axes, whose ax.legend() would otherwise clobber it)
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=colors[s], alpha=0.55,
                      edgecolor=_darken(colors[s]))
        for s in solvers
    ]
    ax.legend(handles, solvers, fontsize=8, loc="upper left")
    if log:
        ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
        ax.yaxis.set_major_formatter(
            ticker.FuncFormatter(
                lambda v, p: f"{10 ** v:g}" if abs(v - round(v)) < 1e-9 else ""
            )
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
    ap.add_argument(
        "--series",
        nargs="+",
        choices=["full", "reduced", "mixed"],
        default=["full", "reduced", "mixed"],
        help="which series to generate (any combination, default: all): "
        "'full' = all 5 solvers; 'reduced' = baseline / best-pocr / best-mtx; "
        "'mixed' = baseline / best-pocr / mtx / adv-mtx",
    )
    args = ap.parse_args()

    data = load_data(args.data)
    os.makedirs(args.out_dir, exist_ok=True)

    # Selection criteria for the reduced best-of series: the winning variant
    # per benchmark is the one with the lowest value of the quantity the
    # figure plots (lower is better).
    sel_time = lambda blk: blk["time"]
    sel_time_sum = lambda blk: blk["time_sum"]
    sel_mem = lambda blk: blk["mem"]

    for g in args.grammars:
        agg, benches = aggregate(data, g, skip_empty=not args.keep_empty)
        # raw per-file values for the violin plots: total time in seconds,
        # peak memory in GB
        raw_time, _ = collect_values(data, g, 1, 1 / 1000.0,
                                     skip_empty=not args.keep_empty)
        raw_mem, _ = collect_values(data, g, 2, 1 / 1024.0 / 1024.0,
                                    skip_empty=not args.keep_empty)
        label = GRAMMAR_LABEL[g]
        if "full" in args.series:
            paths = [
                plot_bars(
                    agg,
                    benches,
                    "time",
                    f"time_{g}.png",
                    "Time (s)",
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
                    "Total time (s)",
                    f"Total runtime per benchmark (sum over files) — {label}",
                    args.out_dir,
                    not args.linear_y,
                ),
                plot_bars(
                    agg,
                    benches,
                    "mem",
                    f"memory_{g}.png",
                    "Peak memory (GB)",
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
                plot_violin(
                    resolve_violin(raw_time, benches, SOLVER_ORDER),
                    benches,
                    f"violin_time_{g}.png",
                    f"Per-file runtime distribution — {label}",
                    args.out_dir,
                    not args.linear_y,
                    solvers=SOLVER_ORDER,
                    colors=SOLVER_COLORS,
                ),
                plot_violin(
                    resolve_violin(raw_mem, benches, SOLVER_ORDER),
                    benches,
                    f"violin_memory_{g}.png",
                    f"Per-file peak memory distribution — {label}",
                    args.out_dir,
                    args.log_y,
                    solvers=SOLVER_ORDER,
                    colors=SOLVER_COLORS,
                    ylabel="Peak memory (GB)",
                ),
            ]
            for p in paths:
                print(f"wrote {p}")
            summarize(agg, benches, g)
        if "reduced" in args.series:
            red_dir = os.path.join(args.out_dir, "reduced")
            os.makedirs(red_dir, exist_ok=True)
            red_paths = [
                plot_bars(
                    resolve_best(agg, benches, sel_time),
                    benches,
                    "time",
                    f"time_{g}.png",
                    "Time (s)",
                    f"Geometric-mean runtime per benchmark (min-max over files, best-of) — {label}",
                    red_dir,
                    not args.linear_y,
                    show_range=True,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_bars(
                    resolve_best(agg, benches, sel_time_sum),
                    benches,
                    "time_sum",
                    f"time_total_{g}.png",
                    "Total time (s)",
                    f"Total runtime per benchmark (sum over files, best-of) — {label}",
                    red_dir,
                    not args.linear_y,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_bars(
                    resolve_best(agg, benches, sel_mem),
                    benches,
                    "mem",
                    f"memory_{g}.png",
                    "Peak memory (GB)",
                    f"Geometric-mean peak memory per benchmark (best-of) — {label}",
                    red_dir,
                    args.log_y,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_ratio(
                    resolve_best(agg, benches, sel_time),
                    benches,
                    g,
                    "time",
                    f"speedup_{g}.png",
                    "Speedup vs baseline (x)",
                    "Speedup vs baseline per benchmark (geometric mean, best-of)",
                    red_dir,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_ratio(
                    resolve_best(agg, benches, sel_time_sum),
                    benches,
                    g,
                    "time_sum",
                    f"speedup_total_{g}.png",
                    "Speedup vs baseline (x)",
                    "Speedup vs baseline per benchmark (total time, best-of)",
                    red_dir,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_ratio(
                    resolve_best(agg, benches, sel_mem),
                    benches,
                    g,
                    "mem",
                    f"memory_ratio_{g}.png",
                    "Memory improvement vs baseline (x)",
                    "Memory improvement vs baseline per benchmark (best-of)",
                    red_dir,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_violin(
                    resolve_violin(raw_time, benches, REDUCED_SOLVERS),
                    benches,
                    f"violin_time_{g}.png",
                    f"Per-file runtime distribution (best-of) — {label}",
                    red_dir,
                    not args.linear_y,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                ),
                plot_violin(
                    resolve_violin(raw_mem, benches, REDUCED_SOLVERS),
                    benches,
                    f"violin_memory_{g}.png",
                    f"Per-file peak memory distribution (best-of) — {label}",
                    red_dir,
                    args.log_y,
                    solvers=REDUCED_SOLVERS,
                    colors=REDUCED_COLORS,
                    ylabel="Peak memory (GB)",
                ),
            ]
            for p in red_paths:
                print(f"wrote {p}")
        if "mixed" in args.series:
            mix_dir = os.path.join(args.out_dir, "mixed")
            os.makedirs(mix_dir, exist_ok=True)
            mix_paths = [
                plot_bars(
                    resolve_best_pocr(agg, benches, sel_time),
                    benches,
                    "time",
                    f"time_{g}.png",
                    "Time (s)",
                    f"Geometric-mean runtime per benchmark (min-max over files, best-pocr) — {label}",
                    mix_dir,
                    not args.linear_y,
                    show_range=True,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_bars(
                    resolve_best_pocr(agg, benches, sel_time_sum),
                    benches,
                    "time_sum",
                    f"time_total_{g}.png",
                    "Total time (s)",
                    f"Total runtime per benchmark (sum over files, best-pocr) — {label}",
                    mix_dir,
                    not args.linear_y,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_bars(
                    resolve_best_pocr(agg, benches, sel_mem),
                    benches,
                    "mem",
                    f"memory_{g}.png",
                    "Peak memory (GB)",
                    f"Geometric-mean peak memory per benchmark (best-pocr) — {label}",
                    mix_dir,
                    args.log_y,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_ratio(
                    resolve_best_pocr(agg, benches, sel_time),
                    benches,
                    g,
                    "time",
                    f"speedup_{g}.png",
                    "Speedup vs baseline (x)",
                    "Speedup vs baseline per benchmark (geometric mean, best-pocr)",
                    mix_dir,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_ratio(
                    resolve_best_pocr(agg, benches, sel_time_sum),
                    benches,
                    g,
                    "time_sum",
                    f"speedup_total_{g}.png",
                    "Speedup vs baseline (x)",
                    "Speedup vs baseline per benchmark (total time, best-pocr)",
                    mix_dir,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_ratio(
                    resolve_best_pocr(agg, benches, sel_mem),
                    benches,
                    g,
                    "mem",
                    f"memory_ratio_{g}.png",
                    "Memory improvement vs baseline (x)",
                    "Memory improvement vs baseline per benchmark (best-pocr)",
                    mix_dir,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_violin(
                    resolve_violin(raw_time, benches, MIXED_SOLVERS),
                    benches,
                    f"violin_time_{g}.png",
                    f"Per-file runtime distribution (best-pocr) — {label}",
                    mix_dir,
                    not args.linear_y,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                ),
                plot_violin(
                    resolve_violin(raw_mem, benches, MIXED_SOLVERS),
                    benches,
                    f"violin_memory_{g}.png",
                    f"Per-file peak memory distribution (best-pocr) — {label}",
                    mix_dir,
                    args.log_y,
                    solvers=MIXED_SOLVERS,
                    colors=MIXED_COLORS,
                    ylabel="Peak memory (GB)",
                ),
            ]
            for p in mix_paths:
                print(f"wrote {p}")


if __name__ == "__main__":
    main()
