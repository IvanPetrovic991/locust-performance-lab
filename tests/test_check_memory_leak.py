def flat(n=40, base=100.0, step=0.01):
    """~flat memory profile: negligible drift."""
    return [(i * 5.0, base + i * step) for i in range(n)]


def climbing(n=40, base=100.0, step=2.0):
    """Linear growth: step MB per 5s sample = step*720 MB/h."""
    return [(i * 5.0, base + i * step) for i in range(n)]


def test_stable_memory_passes(check_memory_leak, make_memory_csv, run_main, capsys):
    assert run_main(check_memory_leak, make_memory_csv(flat())) == 0
    assert "no leak signature" in capsys.readouterr().out


def test_linear_growth_fails(check_memory_leak, make_memory_csv, run_main, capsys):
    assert run_main(check_memory_leak, make_memory_csv(climbing())) == 1
    assert "MEMORY LEAK SUSPECTED" in capsys.readouterr().out


def test_high_slope_but_tiny_total_growth_passes(check_memory_leak, make_memory_csv, run_main):
    """Both conditions must hold: steep-but-short noise (total < 15 MB) is not
    a leak verdict, even though the extrapolated slope crosses the rate gate."""
    samples = climbing(n=12, step=0.9)  # ~648 MB/h slope, ~9 MB total in window
    assert run_main(check_memory_leak, make_memory_csv(samples), "--warmup-pct", "0") == 0


def test_insufficient_samples_after_warmup_exits_2(check_memory_leak, make_memory_csv, run_main):
    assert run_main(check_memory_leak, make_memory_csv(flat(n=11)), "--warmup-pct", "20") == 2


def test_warmup_pct_out_of_range_rejected(check_memory_leak, make_memory_csv, run_main):
    code = run_main(check_memory_leak, make_memory_csv(flat()), "--warmup-pct", "100")
    assert code == 2  # argparse parser.error


def test_thresholds_are_configurable(check_memory_leak, make_memory_csv, run_main):
    csv_path = make_memory_csv(climbing())
    assert run_main(
        check_memory_leak, csv_path,
        "--max-growth-mb-per-hour", "100000", "--min-total-growth-mb", "100000",
    ) == 0
