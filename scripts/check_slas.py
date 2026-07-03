#!/usr/bin/env python3
"""
SLA gate: validate a Locust CSV stats file against config/slas.yml.

Usage:
    python scripts/check_slas.py reports/run_stats.csv [--config config/slas.yml]

Exits 1 if any SLA is breached — wire it into CI to fail the build.
Writes a Markdown summary to $GITHUB_STEP_SUMMARY when running in GitHub Actions.
"""

import argparse
import csv
import os
import sys

import yaml

PASS, FAIL = "PASS", "FAIL"


def to_float(value):
    """Locust writes 'N/A' in percentile columns for zero-request rows."""
    return float(value) if value not in (None, "", "N/A") else 0.0


def load_stats(csv_path):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def check_row(row, limits):
    """Return a list of (metric, actual, limit, verdict) for one stats row."""
    results = []

    p95 = to_float(row["95%"])
    requests = int(row["Request Count"])
    failures = int(row["Failure Count"])
    error_pct = (failures / requests * 100) if requests else 0.0
    rps = to_float(row["Requests/s"])

    if "p95_ms" in limits:
        verdict = PASS if p95 <= limits["p95_ms"] else FAIL
        results.append(("p95 latency", f"{p95:.0f} ms", f"<= {limits['p95_ms']} ms", verdict))
    if "error_rate_pct" in limits:
        verdict = PASS if error_pct <= limits["error_rate_pct"] else FAIL
        results.append(("error rate", f"{error_pct:.2f} %", f"<= {limits['error_rate_pct']} %", verdict))
    if "min_rps" in limits:
        verdict = PASS if rps >= limits["min_rps"] else FAIL
        results.append(("throughput", f"{rps:.1f} rps", f">= {limits['min_rps']} rps", verdict))

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stats_csv", help="Locust *_stats.csv file")
    parser.add_argument("--config", default="config/slas.yml")
    args = parser.parse_args()

    with open(args.config) as f:
        slas = yaml.safe_load(f)

    rows = load_stats(args.stats_csv)
    lines = ["| Scope | Metric | Actual | Limit | Verdict |", "|---|---|---|---|---|"]
    breached = False
    unmatched = set((slas.get("endpoints") or {}).keys())

    for row in rows:
        if row["Name"] == "Aggregated":
            scope, limits = "Aggregated", slas.get("global", {})
        else:
            key = f"{row['Type']} {row['Name']}"
            limits = (slas.get("endpoints") or {}).get(key)
            if not limits:
                continue
            scope = key
            unmatched.discard(key)

        for metric, actual, limit, verdict in check_row(row, limits):
            if verdict == FAIL:
                breached = True
            icon = "✅" if verdict == PASS else "❌"
            lines.append(f"| {scope} | {metric} | {actual} | {limit} | {icon} {verdict} |")

    # An SLA'd endpoint that produced no stats row is a failure, not a free
    # pass — otherwise a renamed task or a typo in slas.yml silently disables
    # that SLA forever.
    for key in sorted(unmatched):
        breached = True
        lines.append(f"| {key} | presence | absent from results | must appear | ❌ {FAIL} |")

    table = "\n".join(lines)
    print(f"\nSLA report for {args.stats_csv}\n\n{table}\n")

    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"## 📊 SLA Gate\n\n{table}\n")

    if breached:
        print("RESULT: SLA BREACH — failing the build.")
        sys.exit(1)
    print("RESULT: all SLAs met.")


if __name__ == "__main__":
    main()
