import csv
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

# Columns the gate scripts read from Locust's *_stats.csv (the real file has
# more; DictReader makes the extras irrelevant).
STATS_COLUMNS = ["Type", "Name", "Request Count", "Failure Count", "Requests/s", "95%"]


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def check_slas():
    return _load_script("check_slas")


@pytest.fixture(scope="session")
def compare_runs():
    return _load_script("compare_runs")


@pytest.fixture(scope="session")
def check_memory_leak():
    return _load_script("check_memory_leak")


@pytest.fixture(scope="session")
def memory_monitor():
    return _load_script("memory_monitor")


@pytest.fixture
def make_stats_csv(tmp_path):
    """Write a Locust-format stats CSV from a list of row dicts."""

    counter = {"n": 0}

    def _make(rows):
        counter["n"] += 1
        path = tmp_path / f"stats_{counter['n']}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=STATS_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return str(path)

    return _make


@pytest.fixture
def make_memory_csv(tmp_path):
    """Write a memory_monitor-format CSV from (elapsed_s, memory_mb) pairs."""

    def _make(samples):
        path = tmp_path / "memory.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["elapsed_s", "memory_mb"])
            writer.writerows(samples)
        return str(path)

    return _make


@pytest.fixture(autouse=True)
def no_github_summary(monkeypatch):
    """Keep gate scripts from appending to the real CI job summary mid-test."""
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)


@pytest.fixture
def run_main(monkeypatch):
    """Run a gate script's main() with the given argv; return its exit code."""

    def _run(module, *argv):
        monkeypatch.setattr(sys, "argv", [module.__name__] + list(argv))
        try:
            module.main()
        except SystemExit as exc:
            return exc.code
        return 0

    return _run
