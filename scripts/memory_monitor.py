#!/usr/bin/env python3
"""
Sample a container's memory usage into a CSV while a load test runs.

Usage:
    python scripts/memory_monitor.py --container <name-or-id> \
        [--interval 5] [--duration 0] [--output reports/memory.csv]

With --duration 0 (default) it samples until killed, so it can be started in
the background, run alongside a soak test, and stopped when the test ends.
Feed the CSV to scripts/check_memory_leak.py afterwards.
"""

import argparse
import csv
import subprocess
import sys
import time

# longest suffix first, so "MiB" is not mistaken for "B"
UNITS = [("GiB", 1024.0), ("MiB", 1.0), ("KiB", 1 / 1024), ("B", 1 / 1024 / 1024)]


def read_memory_mb(container: str) -> float:
    """Return current memory usage in MiB via `docker stats`."""
    out = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    used = out.split("/")[0].strip()  # e.g. "85.3MiB / 7.66GiB"
    for unit, factor in UNITS:
        if used.endswith(unit):
            return float(used[: -len(unit)]) * factor
    raise ValueError(f"Unparseable memory value: {out!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between samples")
    parser.add_argument("--duration", type=float, default=0, help="seconds to run; 0 = until killed")
    parser.add_argument("--output", default="reports/memory.csv")
    args = parser.parse_args()

    start = time.time()
    with open(args.output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["elapsed_s", "memory_mb"])
        while True:
            try:
                mem = read_memory_mb(args.container)
            except (subprocess.CalledProcessError, ValueError) as exc:
                print(f"sample failed, skipping: {exc}", file=sys.stderr)
                time.sleep(args.interval)
                continue
            elapsed = time.time() - start
            writer.writerow([f"{elapsed:.1f}", f"{mem:.2f}"])
            f.flush()
            if args.duration and elapsed >= args.duration:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
