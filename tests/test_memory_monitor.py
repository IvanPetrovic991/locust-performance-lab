import pytest


class FakeCompleted:
    def __init__(self, stdout):
        self.stdout = stdout


@pytest.mark.parametrize("stats_output,expected_mb", [
    ("85.3MiB / 7.66GiB", 85.3),
    ("1.5GiB / 7.66GiB", 1536.0),
    ("512KiB / 7.66GiB", 0.5),
    ("2048B / 7.66GiB", 2048 / 1024 / 1024),
])
def test_mem_usage_parsing(memory_monitor, monkeypatch, stats_output, expected_mb):
    monkeypatch.setattr(
        memory_monitor.subprocess, "run",
        lambda *a, **kw: FakeCompleted(stats_output + "\n"),
    )
    assert memory_monitor.read_memory_mb("whatever") == pytest.approx(expected_mb)


def test_unparseable_output_raises(memory_monitor, monkeypatch):
    monkeypatch.setattr(
        memory_monitor.subprocess, "run",
        lambda *a, **kw: FakeCompleted("-- / --"),
    )
    with pytest.raises(ValueError):
        memory_monitor.read_memory_mb("whatever")
