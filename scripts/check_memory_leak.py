#!/usr/bin/env python3
"""
Memory-leak gate: analyze the memory trend recorded by memory_monitor.py.

Usage:
    python scripts/check_memory_leak.py reports/memory.csv \
        [--max-growth-mb-per-hour 50] [--min-total-growth-mb 15] [--warmup-pct 20]

The first --warmup-pct of samples is discarded (caches filling up, JIT, pool
warm-up — normal growth that is not a leak). A least-squares line is then
fitted through the remaining samples. A leak is reported only when BOTH hold:

  * the fitted slope exceeds --max-growth-mb-per-hour, and
  * total growth across the analyzed window exceeds --min-total-growth-mb
    (guards against extrapolating noise from short runs).

Exits 1 on a detected leak. Writes a Markdown summary to $GITHUB_STEP_SUMMARY
when running in GitHub Actions.
"""

import argparse
import csv
import os
import sys


def load_samples(csv_path):
    with open(csv_path, newline="") as f:
        return [(float(r["elapsed_s"]), float(r["memory_mb"])) for r in csv.DictReader(f)]


def fit_slope(samples):
    """Least-squares slope in MB/s through (elapsed_s, memory_mb) points."""
    n = len(samples)
    mean_x = sum(x for x, _ in samples) / n
    mean_y = sum(y for _, y in samples) / n
    var_x = sum((x - mean_x) ** 2 for x, _ in samples)
    if var_x == 0:
        return 0.0
    cov_xy = sum((x - mean_x) * (y - mean_y) for x, y in samples)
    return cov_xy / var_x


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("memory_csv", help="CSV produced by memory_monitor.py")
    parser.add_argument("--max-growth-mb-per-hour", type=float, default=50.0)
    parser.add_argument("--min-total-growth-mb", type=float, default=15.0)
    parser.add_argument("--warmup-pct", type=float, default=20.0)
    args = parser.parse_args()

    samples = load_samples(args.memory_csv)
    if len(samples) < 10:
        print(f"ERROR: only {len(samples)} samples — need at least 10 for a meaningful trend.")
        sys.exit(2)

    analyzed = samples[int(len(samples) * args.warmup_pct / 100):]
    slope_mb_h = fit_slope(analyzed) * 3600
    total_growth = analyzed[-1][1] - analyzed[0][1]
    window_min = (analyzed[-1][0] - analyzed[0][0]) / 60

    leaking = slope_mb_h > args.max_growth_mb_per_hour and total_growth > args.min_total_growth_mb

    rows = [
        ("analyzed window", f"{window_min:.1f} min ({len(analyzed)} samples, warm-up excluded)"),
        ("memory at window start", f"{analyzed[0][1]:.1f} MB"),
        ("memory at window end", f"{analyzed[-1][1]:.1f} MB"),
        ("total growth", f"{total_growth:+.1f} MB (gate: > {args.min_total_growth_mb} MB)"),
        ("fitted growth rate", f"{slope_mb_h:+.1f} MB/h (gate: > {args.max_growth_mb_per_hour} MB/h)"),
        ("verdict", "❌ MEMORY LEAK SUSPECTED" if leaking else "✅ STABLE"),
    ]
    table = "\n".join(["| Metric | Value |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows])
    print(f"\nMemory trend report for {args.memory_csv}\n\n{table}\n")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## 🧠 Memory leak gate\n\n{table}\n")

    if leaking:
        print("RESULT: memory grows linearly under constant load — investigate before it OOMs in production.")
        sys.exit(1)
    print("RESULT: no leak signature detected.")


if __name__ == "__main__":
    main()
