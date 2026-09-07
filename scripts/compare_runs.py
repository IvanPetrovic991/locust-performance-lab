#!/usr/bin/env python3
"""
Regression detector: compare a run against one or more baseline runs.

Usage:
    # classic two-run diff
    python scripts/compare_runs.py reports/baseline_stats.csv reports/current_stats.csv

    # median of several baseline runs (recommended in CI)
    python scripts/compare_runs.py baselines/*/ci_stats.csv reports/ci_stats.csv \
        [--p95-tolerance-pct 15] [--rps-tolerance-pct 15]

A regression is flagged when, for any endpoint present in both the baseline and
the current run:
  * p95 latency grew by more than the tolerance, or
  * throughput (req/s) dropped by more than the tolerance, or
  * the error rate got worse by more than 0.5 percentage points.

Exits 1 on regression — use it in CI to compare a nightly run against recent
history, or locally to validate that a tuning change actually helped.

Why several baselines? One run is one sample of a noisy process. On a shared CI
runner the run-to-run spread of p95 easily exceeds any tolerance worth setting,
so a single-run baseline produces a coin-flip gate. Worse, picking "the last run
that passed" ratchets the baseline towards the fastest run ever recorded: after
one lucky-fast night every following night is measured against that outlier.
The median of the last N runs has neither problem — it is a stable reference
that tracks the environment instead of chasing its best case.
"""

import argparse
import csv
import os
import statistics
import sys


def to_float(value):
    """Locust writes 'N/A' in percentile columns for zero-request rows."""
    return float(value) if value not in (None, "", "N/A") else 0.0


def load_stats(csv_path):
    with open(csv_path, newline="") as f:
        rows = {}
        for row in csv.DictReader(f):
            key = "Aggregated" if row["Name"] == "Aggregated" else f"{row['Type']} {row['Name']}"
            requests = int(row["Request Count"])
            rows[key] = {
                "requests": requests,
                "p95": to_float(row["95%"]),
                "rps": to_float(row["Requests/s"]),
                "error_pct": (int(row["Failure Count"]) / requests * 100) if requests else 0.0,
            }
        return rows


def merge_baselines(runs):
    """Collapse N baseline runs into one per-endpoint median reference.

    Endpoints are merged independently, using only the runs that actually
    contain them — a run that never exercised an endpoint should not drag its
    median towards zero. Request counts use the minimum instead of the median,
    so the low-sample guard stays conservative: if any baseline run barely
    touched an endpoint, its percentiles are treated as thin everywhere.
    """
    merged = {}
    for key in {k for run in runs for k in run}:
        present = [run[key] for run in runs if key in run]
        merged[key] = {
            "runs": len(present),
            "requests": min(r["requests"] for r in present),
            "p95": statistics.median(r["p95"] for r in present),
            "rps": statistics.median(r["rps"] for r in present),
            "error_pct": statistics.median(r["error_pct"] for r in present),
        }
    return merged


def pct_change(old, new):
    """Percent change; going from 0 to anything is infinite, not 0%."""
    if old:
        return (new - old) / old * 100
    return 0.0 if new == 0 else float("inf")


def fmt_pct(value):
    return "n/a (base 0)" if value == float("inf") else f"{value:+.1f} %"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_csv", nargs="+",
                        help="one or more baseline stats CSVs; with several, the "
                             "per-endpoint median is used as the reference")
    parser.add_argument("current_csv", help="the run being judged")
    parser.add_argument("--p95-tolerance-pct", type=float, default=15.0)
    parser.add_argument("--rps-tolerance-pct", type=float, default=15.0)
    parser.add_argument("--error-rate-tolerance-pp", type=float, default=0.5,
                        help="max allowed error-rate increase, in percentage points")
    parser.add_argument("--min-requests", type=int, default=20,
                        help="endpoints with fewer requests in either run are reported "
                             "but not gated — percentiles from a handful of samples are "
                             "quantization noise, not evidence")
    parser.add_argument("--advisory", action="store_true",
                        help="report regressions but always exit 0 — for trend runs "
                             "whose job is to inform, not to block")
    args = parser.parse_args()

    baseline_runs = [load_stats(path) for path in args.baseline_csv]
    baseline = merge_baselines(baseline_runs)
    current = load_stats(args.current_csv)

    lines = [
        "| Endpoint | p95 (base → now) | Δ p95 | rps (base → now) | Δ rps | errors (base → now) | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    regressed = False

    for key in sorted(baseline, key=lambda k: (k == "Aggregated", k)):
        if key not in current:
            lines.append(f"| {key} | — | — | — | — | — | ⚪ GONE (absent from this run) |")
            continue
        b, c = baseline[key], current[key]
        d_p95 = pct_change(b["p95"], c["p95"])
        d_rps = pct_change(b["rps"], c["rps"])
        d_err = c["error_pct"] - b["error_pct"]

        low_sample = min(b["requests"], c["requests"]) < args.min_requests
        bad = not low_sample and (
            d_p95 > args.p95_tolerance_pct
            or d_rps < -args.rps_tolerance_pct
            or d_err > args.error_rate_tolerance_pp
        )
        regressed = regressed or bad

        verdict = "⚪ LOW SAMPLE" if low_sample else ("❌ REGRESSED" if bad else "✅ OK")
        lines.append(
            f"| {key} "
            f"| {b['p95']:.0f} → {c['p95']:.0f} ms | {fmt_pct(d_p95)} "
            f"| {b['rps']:.1f} → {c['rps']:.1f} | {fmt_pct(d_rps)} "
            f"| {b['error_pct']:.2f} → {c['error_pct']:.2f} % "
            f"| {verdict} |"
        )

    # Endpoints the current run produced that no baseline had: usually a new
    # task, occasionally a renamed one that silently dropped its history.
    for key in sorted(set(current) - set(baseline)):
        lines.append(f"| {key} | — → {current[key]['p95']:.0f} ms | — | — → "
                     f"{current[key]['rps']:.1f} | — | — → "
                     f"{current[key]['error_pct']:.2f} % | 🆕 NEW (no baseline) |")

    n = len(baseline_runs)
    reference = (f"median of {n} baseline runs" if n > 1 else args.baseline_csv[0])
    table = "\n".join(lines)
    print(f"\nRegression report: {args.current_csv} vs {reference}\n\n{table}\n")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            heading = "📉 Regression check (advisory)" if args.advisory else "📉 Regression check"
            f.write(f"## {heading}\n\nBaseline: {reference}.\n\n{table}\n")

    if regressed:
        print("RESULT: performance regression detected.")
        if args.advisory:
            print("(advisory mode — not failing the build)")
            return
        sys.exit(1)
    print("RESULT: no regression versus baseline.")


if __name__ == "__main__":
    main()
