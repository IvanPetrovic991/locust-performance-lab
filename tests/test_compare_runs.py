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
