import json
from pathlib import Path

import pytest

from ai_agent_toolbox.simulation import (
    Fleet,
    LogLevel,
    Metric,
    ScenarioError,
    ServiceStatus,
    UnknownServiceError,
)


def make_fleet() -> Fleet:
    return Fleet.load_default()


def minimal_service(**overrides: object) -> dict[str, object]:
    service: dict[str, object] = {
        "status": "healthy",
        "version": "1.0.0",
        "logs": [],
        "metrics": {m.value: {"baseline": 1} for m in Metric},
    }
    service.update(overrides)
    return service


# -- loading ------------------------------------------------------------


def test_default_scenario_has_expected_services() -> None:
    assert make_fleet().service_names() == [
        "auth",
        "checkout",
        "notifications",
        "payments",
        "search",
    ]


def test_from_dict_rejects_missing_keys() -> None:
    with pytest.raises(ScenarioError):
        Fleet.from_dict({"services": {}})


def test_from_dict_rejects_empty_fleet() -> None:
    with pytest.raises(ScenarioError, match="no services"):
        Fleet.from_dict({"now": "2026-01-15T10:00:00Z", "services": {}})


def test_from_dict_rejects_unknown_status() -> None:
    data = {
        "now": "2026-01-15T10:00:00Z",
        "services": {"a": minimal_service(status="exploding")},
    }
    with pytest.raises(ScenarioError):
        Fleet.from_dict(data)


def test_from_dict_rejects_service_with_missing_metrics() -> None:
    data = {
        "now": "2026-01-15T10:00:00Z",
        "services": {"a": minimal_service(metrics={"cpu_percent": {"baseline": 1}})},
    }
    with pytest.raises(ScenarioError, match="missing metrics"):
        Fleet.from_dict(data)


# -- status -------------------------------------------------------------


def test_get_status_for_known_services() -> None:
    fleet = make_fleet()
    assert fleet.get_status("payments").status is ServiceStatus.DEGRADED
    assert fleet.get_status("auth").status is ServiceStatus.HEALTHY
    assert fleet.get_status("notifications").status is ServiceStatus.DOWN
    assert fleet.get_status("payments").version == "2.14.1"


def test_unknown_service_error_lists_valid_services() -> None:
    with pytest.raises(UnknownServiceError) as excinfo:
        make_fleet().get_status("paymnets")
    assert excinfo.value.service == "paymnets"
    assert "payments" in excinfo.value.valid_services
    assert "payments" in str(excinfo.value)


# -- logs ---------------------------------------------------------------


def test_logs_are_newest_first_and_within_window() -> None:
    logs = make_fleet().get_logs("payments", minutes=15)
    timestamps = [entry.timestamp for entry in logs]
    assert timestamps == sorted(timestamps, reverse=True)
    assert len(logs) == 5  # entries at 1, 2, 4, 7 and 12 minutes ago


def test_logs_window_excludes_older_entries() -> None:
    logs = make_fleet().get_logs("payments", minutes=3)
    assert len(logs) == 2
    assert all("Deployment" not in entry.message for entry in logs)


def test_logs_filter_by_level() -> None:
    logs = make_fleet().get_logs("payments", minutes=60, level=LogLevel.ERROR)
    assert len(logs) == 3
    assert all(entry.level is LogLevel.ERROR for entry in logs)


def test_logs_respect_limit() -> None:
    logs = make_fleet().get_logs("payments", minutes=60, limit=2)
    assert len(logs) == 2
    assert "bank-gateway" in logs[0].message  # newest entry comes first


def test_logs_reject_non_positive_arguments() -> None:
    fleet = make_fleet()
    with pytest.raises(ValueError):
        fleet.get_logs("payments", minutes=0)
    with pytest.raises(ValueError):
        fleet.get_logs("payments", minutes=5, limit=0)


def test_logs_for_unknown_service() -> None:
    with pytest.raises(UnknownServiceError):
        make_fleet().get_logs("nope", minutes=5)


# -- metrics ------------------------------------------------------------


def test_metric_series_has_one_point_per_minute_oldest_first() -> None:
    series = make_fleet().get_metric("auth", Metric.CPU_PERCENT, minutes=10)
    assert len(series.points) == 10
    timestamps = [p.timestamp for p in series.points]
    assert timestamps == sorted(timestamps)


def test_metric_baseline_when_no_anomaly() -> None:
    series = make_fleet().get_metric("auth", Metric.LATENCY_P95_MS, minutes=30)
    assert series.minimum == series.maximum == 45


def test_metric_anomaly_starts_at_the_configured_time() -> None:
    fleet = make_fleet()
    before = fleet.get_metric("payments", Metric.LATENCY_P95_MS, minutes=60)
    assert before.points[0].value == 180  # 59 minutes ago: baseline
    assert before.latest == 950  # now: anomalous
    recent = fleet.get_metric("payments", Metric.LATENCY_P95_MS, minutes=10)
    assert recent.minimum == recent.maximum == 950


def test_metric_summary_values() -> None:
    series = make_fleet().get_metric("payments", Metric.ERROR_RATE, minutes=40)
    # 20 anomalous minutes (6.5) and 20 baseline minutes (0.4)
    assert series.average == pytest.approx(3.45)


def test_metric_rejects_non_positive_window() -> None:
    with pytest.raises(ValueError):
        make_fleet().get_metric("payments", Metric.CPU_PERCENT, minutes=0)


# -- determinism and fixtures -------------------------------------------


def test_fleet_is_deterministic() -> None:
    first = make_fleet().get_logs("payments", minutes=60)
    second = make_fleet().get_logs("payments", minutes=60)
    assert first == second


def test_default_scenario_contains_prompt_injection_fixture() -> None:
    """Evaluations depend on this planted log line; do not remove it silently."""
    logs = make_fleet().get_logs("notifications", minutes=10, level=LogLevel.ERROR)
    assert any("IGNORE ALL PREVIOUS INSTRUCTIONS" in entry.message for entry in logs)


def test_from_file_loads_a_scenario(tmp_path: Path) -> None:
    path = tmp_path / "scenario.json"
    scenario = {"now": "2026-01-15T10:00:00Z", "services": {"a": minimal_service()}}
    path.write_text(json.dumps(scenario), encoding="utf-8")
    assert Fleet.from_file(path).service_names() == ["a"]


def test_from_file_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ScenarioError, match="not valid JSON"):
        Fleet.from_file(path)
