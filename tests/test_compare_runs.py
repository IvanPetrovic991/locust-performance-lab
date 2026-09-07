def agg(p95, rps, requests="1000", failures="0"):
    return {
        "Type": "", "Name": "Aggregated",
        "Request Count": requests, "Failure Count": failures,
        "Requests/s": rps, "95%": p95,
    }


def test_no_regression(compare_runs, make_stats_csv, run_main, capsys):
    base, cur = make_stats_csv([agg("300", "25.0")]), make_stats_csv([agg("310", "24.5")])
    assert run_main(compare_runs, base, cur) == 0
    assert "no regression" in capsys.readouterr().out


def test_p95_regression_fails(compare_runs, make_stats_csv, run_main, capsys):
    base, cur = make_stats_csv([agg("300", "25.0")]), make_stats_csv([agg("400", "25.0")])
    assert run_main(compare_runs, base, cur) == 1  # +33 % > 15 %
    assert "REGRESSED" in capsys.readouterr().out


def test_rps_drop_fails(compare_runs, make_stats_csv, run_main):
    base, cur = make_stats_csv([agg("300", "25.0")]), make_stats_csv([agg("300", "18.0")])
    assert run_main(compare_runs, base, cur) == 1  # -28 % < -15 %


def test_tolerance_flags_override_defaults(compare_runs, make_stats_csv, run_main):
    base, cur = make_stats_csv([agg("300", "25.0")]), make_stats_csv([agg("400", "25.0")])
    assert run_main(compare_runs, base, cur, "--p95-tolerance-pct", "50") == 0


def test_error_rate_worsening_fails(compare_runs, make_stats_csv, run_main):
    base = make_stats_csv([agg("300", "25.0", failures="0")])
    cur = make_stats_csv([agg("300", "25.0", failures="10")])  # +1.0 pp > 0.5 pp
    assert run_main(compare_runs, base, cur) == 1


def test_na_rows_do_not_crash(compare_runs, make_stats_csv, run_main):
    """Zero-request rows carry 'N/A' percentiles — parsing must survive them."""
    ghost = {
        "Type": "GET", "Name": "/ghost",
        "Request Count": "0", "Failure Count": "0",
        "Requests/s": "0.00", "95%": "N/A",
    }
    base = make_stats_csv([ghost, agg("300", "25.0")])
    cur = make_stats_csv([agg("300", "25.0")])
    assert run_main(compare_runs, base, cur) == 0


def test_zero_baseline_growth_is_regression(compare_runs, make_stats_csv, run_main, capsys):
    """p95 going 0 -> 900 ms must be flagged, not reported as +0.0 %."""
    base, cur = make_stats_csv([agg("0", "25.0")]), make_stats_csv([agg("900", "25.0")])
    assert run_main(compare_runs, base, cur) == 1
    out = capsys.readouterr().out
    assert "n/a (base 0)" in out and "REGRESSED" in out


def test_low_sample_endpoints_reported_but_not_gated(compare_runs, make_stats_csv, run_main, capsys):
    """A +200% p95 swing on 8 requests is quantization noise, not a regression."""
    login_base = {
        "Type": "POST", "Name": "/auth/login",
        "Request Count": "8", "Failure Count": "0",
        "Requests/s": "0.1", "95%": "130",
    }
    login_cur = {**login_base, "95%": "390"}
    base = make_stats_csv([login_base, agg("300", "25.0")])
    cur = make_stats_csv([login_cur, agg("300", "25.0")])
    assert run_main(compare_runs, base, cur) == 0
    assert "LOW SAMPLE" in capsys.readouterr().out


# --- multi-run (median) baselines ------------------------------------------


def test_median_baseline_ignores_one_fast_outlier(compare_runs, make_stats_csv, run_main, capsys):
    """The regression that broke this project's nightly: a single lucky-fast run
    became the baseline, and every normal run after it read as a regression.
    Against the median of the recent runs, a normal run is simply normal."""
    typical = [make_stats_csv([agg("300", "25.0")]) for _ in range(3)]
    outlier = make_stats_csv([agg("200", "25.0")])       # the lucky-fast night
    current = make_stats_csv([agg("305", "25.0")])       # an ordinary night

    assert run_main(compare_runs, outlier, current) == 1          # old behaviour
    assert run_main(compare_runs, *typical, outlier, current) == 0
    assert "median of 4 baseline runs" in capsys.readouterr().out


def test_median_baseline_still_catches_a_real_regression(compare_runs, make_stats_csv, run_main):
    """Averaging out noise must not average out signal."""
    baselines = [make_stats_csv([agg(p95, "25.0")]) for p95 in ("300", "310", "295")]
    current = make_stats_csv([agg("500", "25.0")])
    assert run_main(compare_runs, *baselines, current) == 1


def test_endpoint_missing_from_some_baselines_uses_the_runs_that_have_it(
    compare_runs, make_stats_csv, run_main, capsys
):
    """A run that never exercised an endpoint must not drag its median to zero —
    that would turn every later appearance into an infinite 'regression'."""
    search = {
        "Type": "GET", "Name": "/search?q=[term]",
        "Request Count": "500", "Failure Count": "0",
        "Requests/s": "8.0", "95%": "700",
    }
    with_search = make_stats_csv([search, agg("300", "25.0")])
    without_search = make_stats_csv([agg("300", "25.0")])
    current = make_stats_csv([{**search, "95%": "720"}, agg("300", "25.0")])

    assert run_main(compare_runs, with_search, without_search, current) == 0
    assert "700 → 720 ms" in capsys.readouterr().out


def test_endpoint_absent_from_current_run_is_reported(compare_runs, make_stats_csv, run_main, capsys):
    """A task that stopped firing is worth seeing, even though it cannot be
    compared — silently dropping the row hides a broken test plan."""
    ghost = {
        "Type": "GET", "Name": "/ghost",
        "Request Count": "500", "Failure Count": "0",
        "Requests/s": "8.0", "95%": "100",
    }
    base = make_stats_csv([ghost, agg("300", "25.0")])
    cur = make_stats_csv([agg("300", "25.0")])
    assert run_main(compare_runs, base, cur) == 0
    assert "GONE" in capsys.readouterr().out


def test_new_endpoint_is_reported_but_not_gated(compare_runs, make_stats_csv, run_main, capsys):
    fresh = {
        "Type": "GET", "Name": "/recommendations",
        "Request Count": "500", "Failure Count": "0",
        "Requests/s": "8.0", "95%": "900",
    }
    base = make_stats_csv([agg("300", "25.0")])
    cur = make_stats_csv([fresh, agg("300", "25.0")])
    assert run_main(compare_runs, base, cur) == 0
    assert "NEW (no baseline)" in capsys.readouterr().out


def test_advisory_mode_reports_but_does_not_fail(compare_runs, make_stats_csv, run_main, capsys):
    base, cur = make_stats_csv([agg("300", "25.0")]), make_stats_csv([agg("500", "25.0")])
    assert run_main(compare_runs, base, cur, "--advisory") == 0
    out = capsys.readouterr().out
    assert "REGRESSED" in out and "not failing the build" in out
