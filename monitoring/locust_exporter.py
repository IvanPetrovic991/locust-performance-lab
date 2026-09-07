#!/usr/bin/env python3
"""
Prometheus exporter for a running Locust master.

Locust's web UI exposes live statistics as JSON at /stats/requests. This turns
that into Prometheus exposition format, including the metric the off-the-shelf
exporters get wrong: per-endpoint response-time percentiles.

Why not use containersol/locust_exporter? Two reasons, both verified against a
live master:

  * its percentile gauges report 0. It reads a flat top-level key that Locust
    no longer publishes — current percentiles now arrive nested, as
    {"current_response_time_percentiles": {"response_time_percentile_0.95": 89}},
    and every per-endpoint row carries its own "response_time_percentile_0.95".
    Percentiles are the whole point of a latency dashboard; a load-testing
    project cannot ship one that silently reads zero.
  * it is published for linux/amd64 only, so on an arm64 machine (any recent
    Mac) it runs under emulation.

Stdlib only, so it runs on the stock python image with the file mounted in —
no build step, no dependency to keep pinned.

Environment:
    LOCUST_URI      master web UI base URL   (default http://locust-master:8089)
    EXPORTER_PORT   port to serve /metrics   (default 9646)
    SCRAPE_TIMEOUT  seconds per upstream request (default 5)
"""

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

LOCUST_URI = os.getenv("LOCUST_URI", "http://locust-master:8089").rstrip("/")
EXPORTER_PORT = int(os.getenv("EXPORTER_PORT", "9646"))
SCRAPE_TIMEOUT = float(os.getenv("SCRAPE_TIMEOUT", "5"))

# (metric suffix, JSON key on each stats row, help text)
ROW_METRICS = [
    ("num_requests", "num_requests", "Total requests issued"),
    ("num_failures", "num_failures", "Total failed requests"),
    ("current_rps", "current_rps", "Requests per second, current window"),
    ("current_fail_per_sec", "current_fail_per_sec", "Failures per second, current window"),
    ("avg_response_time", "avg_response_time", "Mean response time in ms"),
    ("min_response_time", "min_response_time", "Fastest response in ms"),
    ("max_response_time", "max_response_time", "Slowest response in ms"),
    ("median_response_time", "median_response_time", "Median (p50) response time in ms"),
    ("p95_response_time", "response_time_percentile_0.95", "95th percentile response time in ms"),
    ("p99_response_time", "response_time_percentile_0.99", "99th percentile response time in ms"),
]

# Locust publishes percentiles twice, and the difference matters:
#
#   * every stats row carries response_time_percentile_0.95 — CUMULATIVE since
#     the test started. That is the number an SLA should be judged on, and the
#     one that matches the 95% column of the CSV report.
#   * current_response_time_percentiles is a 10-second SLIDING WINDOW over the
#     whole run. That is the number to plot against a user ramp, because it
#     rises and decays with load instead of being dragged flat by history.
#
# Charting the cumulative value against a ramp is a classic way to produce a
# latency graph that looks reassuring and means nothing, so both are exported.
WINDOW_METRICS = [
    ("current_p50_response_time", "response_time_percentile_0.5",
     "Median response time in ms over the last 10 seconds"),
    ("current_p95_response_time", "response_time_percentile_0.95",
     "95th percentile response time in ms over the last 10 seconds"),
]


def escape(value: str) -> str:
    """Escape a Prometheus label value — endpoint names contain quotes rarely,
    but backslashes and newlines would corrupt the exposition format."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def fetch_stats():
    url = f"{LOCUST_URI}/stats/requests"
    with urllib.request.urlopen(url, timeout=SCRAPE_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def render(stats) -> str:
    out = [
        "# HELP locust_up Whether the Locust master responded to this scrape",
        "# TYPE locust_up gauge",
        "locust_up 1",
        "# HELP locust_users Number of simulated users currently running",
        "# TYPE locust_users gauge",
        f"locust_users {stats.get('user_count', 0)}",
        "# HELP locust_workers_count Connected worker processes",
        "# TYPE locust_workers_count gauge",
        f"locust_workers_count {stats.get('worker_count', 0)}",
        "# HELP locust_running Whether a test is currently running",
        "# TYPE locust_running gauge",
        f"locust_running {1 if stats.get('state') == 'running' else 0}",
    ]

    for suffix, key, help_text in ROW_METRICS:
        metric = f"locust_requests_{suffix}"
        out.append(f"# HELP {metric} {help_text}")
        out.append(f"# TYPE {metric} gauge")
        for row in stats.get("stats", []):
            value = row.get(key)
            if value is None:
                continue
            labels = f'name="{escape(row.get("name", ""))}",method="{escape(row.get("method", ""))}"'
            out.append(f"{metric}{{{labels}}} {value}")

    window = stats.get("current_response_time_percentiles") or {}
    for suffix, key, help_text in WINDOW_METRICS:
        value = window.get(key)
        if value is None:
            continue
        metric = f"locust_requests_{suffix}"
        out.append(f"# HELP {metric} {help_text}")
        out.append(f"# TYPE {metric} gauge")
        out.append(f"{metric} {value}")

    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server's required spelling
        if self.path.split("?")[0] not in ("/metrics", "/"):
            self.send_error(404)
            return
        try:
            body = render(fetch_stats())
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            # A master that is down or restarting is a normal state during a
            # test run — report it as a metric rather than failing the scrape.
            body = ("# HELP locust_up Whether the Locust master responded to this scrape\n"
                    "# TYPE locust_up gauge\n"
                    "locust_up 0\n")
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass  # a scrape every 5s would otherwise drown the container log


if __name__ == "__main__":
    print(f"Exporting {LOCUST_URI}/stats/requests on :{EXPORTER_PORT}/metrics", flush=True)
    HTTPServer(("", EXPORTER_PORT), Handler).serve_forever()
