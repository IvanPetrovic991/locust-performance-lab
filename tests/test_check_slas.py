AGGREGATED_OK = {
    "Type": "", "Name": "Aggregated",
    "Request Count": "1000", "Failure Count": "5",
    "Requests/s": "25.0", "95%": "300",
}


def write_slas(tmp_path, content):
    path = tmp_path / "slas.yml"
    path.write_text(content)
    return str(path)


BASIC_SLAS = """
global:
  p95_ms: 800
  error_rate_pct: 2.0
  min_rps: 3
"""


def test_all_slas_met(check_slas, make_stats_csv, run_main, tmp_path, capsys):
    stats = make_stats_csv([AGGREGATED_OK])
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, BASIC_SLAS))
    assert code == 0
    assert "all SLAs met" in capsys.readouterr().out


def test_p95_breach_fails(check_slas, make_stats_csv, run_main, tmp_path, capsys):
    stats = make_stats_csv([{**AGGREGATED_OK, "95%": "1200"}])
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, BASIC_SLAS))
    assert code == 1
    assert "SLA BREACH" in capsys.readouterr().out


def test_error_rate_breach_fails(check_slas, make_stats_csv, run_main, tmp_path):
    stats = make_stats_csv([{**AGGREGATED_OK, "Failure Count": "100"}])  # 10 %
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, BASIC_SLAS))
    assert code == 1


def test_zero_request_run_with_na_percentiles(check_slas, make_stats_csv, run_main, tmp_path, capsys):
    """Locust writes 'N/A' percentiles for zero-request rows — the gate must
    produce a clean FAIL (min_rps), not a ValueError traceback."""
    stats = make_stats_csv([{
        "Type": "", "Name": "Aggregated",
        "Request Count": "0", "Failure Count": "0",
        "Requests/s": "0.00", "95%": "N/A",
    }])
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, BASIC_SLAS))
    assert code == 1
    out = capsys.readouterr().out
    assert "throughput" in out and "FAIL" in out


def test_missing_slad_endpoint_fails(check_slas, make_stats_csv, run_main, tmp_path, capsys):
    """An endpoint with an SLA that never appears in the results must FAIL the
    gate — a typo in slas.yml must not silently disable the SLA."""
    slas = BASIC_SLAS + """
endpoints:
  "POST /checkout":
    p95_ms: 1500
"""
    stats = make_stats_csv([AGGREGATED_OK])  # no /checkout row
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, slas))
    assert code == 1
    assert "absent from results" in capsys.readouterr().out


def test_endpoint_override_checked(check_slas, make_stats_csv, run_main, tmp_path):
    slas = BASIC_SLAS + """
endpoints:
  "POST /checkout":
    p95_ms: 1500
"""
    checkout = {
        "Type": "POST", "Name": "/checkout",
        "Request Count": "100", "Failure Count": "0",
        "Requests/s": "2.0", "95%": "1600",
    }
    stats = make_stats_csv([checkout, AGGREGATED_OK])
    code = run_main(check_slas, stats, "--config", write_slas(tmp_path, slas))
    assert code == 1
