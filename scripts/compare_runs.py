#!/usr/bin/env python3
"""
Regression detector: compare two Locust CSV stats files endpoint by endpoint.

Usage:
    python scripts/compare_runs.py reports/baseline_stats.csv reports/current_stats.csv \
        [--p95-tolerance-pct 15] [--rps-tolerance-pct 15]

A regression is flagged when, for any endpoint present in both runs:
  * p95 latency grew by more than the tolerance, or
  * throughput (req/s) dropped by more than the tolerance, or
  * the error rate got worse by more than 0.5 percentage points.

Exits 1 on regression — use it in CI to compare a PR run against a stored
baseline, or locally to validate a tuning change actually helped.
"""

import argparse
import csv
import os
import sys

ERROR_RATE_TOLERANCE_PP = 0.5


def load_stats(csv_path):
    with open(csv_path, newline="") as f:
        rows = {}
        for row in csv.DictReader(f):
            key = "Aggregated" if row["Name"] == "Aggregated" else f"{row['Type']} {row['Name']}"
            requests = int(row["Request Count"])
            rows[key] = {
                "p95": float(row["95%"] or 0),
                "rps": float(row["Requests/s"]),
                "error_pct": (int(row["Failure Count"]) / requests * 100) if requests else 0.0,
            }
        return rows


def pct_change(old, new):
    return ((new - old) / old * 100) if old else 0.0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_csv")
    parser.add_argument("current_csv")
    parser.add_argument("--p95-tolerance-pct", type=float, default=15.0)
    parser.add_argument("--rps-tolerance-pct", type=float, default=15.0)
    args = parser.parse_args()

    baseline = load_stats(args.baseline_csv)
    current = load_stats(args.current_csv)

    lines = [
        "| Endpoint | p95 (base → now) | Δ p95 | rps (base → now) | Δ rps | errors (base → now) | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    regressed = False

    for key in sorted(baseline, key=lambda k: (k == "Aggregated", k)):
        if key not in current:
            continue
        b, c = baseline[key], current[key]
        d_p95 = pct_change(b["p95"], c["p95"])
        d_rps = pct_change(b["rps"], c["rps"])
        d_err = c["error_pct"] - b["error_pct"]

        bad = (
            d_p95 > args.p95_tolerance_pct
            or d_rps < -args.rps_tolerance_pct
            or d_err > ERROR_RATE_TOLERANCE_PP
        )
        regressed = regressed or bad

        lines.append(
            f"| {key} "
            f"| {b['p95']:.0f} → {c['p95']:.0f} ms | {d_p95:+.1f} % "
            f"| {b['rps']:.1f} → {c['rps']:.1f} | {d_rps:+.1f} % "
            f"| {b['error_pct']:.2f} → {c['error_pct']:.2f} % "
            f"| {'❌ REGRESSED' if bad else '✅ OK'} |"
        )

    table = "\n".join(lines)
    print(f"\nRegression report: {args.baseline_csv} vs {args.current_csv}\n\n{table}\n")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## 📉 Regression check\n\n{table}\n")

    if regressed:
        print("RESULT: performance regression detected.")
        sys.exit(1)
    print("RESULT: no regression versus baseline.")


if __name__ == "__main__":
    main()
