"""Deterministic simulated service fleet.

The fleet is the "world" the agent's tools observe. It is built from a
scenario file and never touches real infrastructure. Everything is a pure
function of the scenario, so tests and evaluations are repeatable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from importlib import resources
from pathlib import Path
from typing import Any


class ServiceStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class Metric(StrEnum):
    LATENCY_P95_MS = "latency_p95_ms"
    ERROR_RATE = "error_rate"
    CPU_PERCENT = "cpu_percent"
    MEMORY_PERCENT = "memory_percent"
    REQUEST_RATE = "request_rate"


class SimulationError(Exception):
    """Base class for simulation errors."""


class ScenarioError(SimulationError):
    """The scenario data is malformed."""


class UnknownServiceError(SimulationError):
    """The requested service does not exist in the fleet."""

    def __init__(self, service: str, valid_services: list[str]) -> None:
        self.service = service
        self.valid_services = valid_services
        super().__init__(
            f"Unknown service {service!r}. Valid services: {', '.join(valid_services)}"
        )


@dataclass(frozen=True)
class StatusReport:
    service: str
    status: ServiceStatus
    version: str
    last_checked: datetime


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    level: LogLevel
    message: str


@dataclass(frozen=True)
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass(frozen=True)
class MetricSeries:
    """Per-minute values, ordered oldest to newest."""

    service: str
    metric: Metric
    points: tuple[MetricPoint, ...]

    @property
    def latest(self) -> float:
        return self.points[-1].value

    @property
    def minimum(self) -> float:
        return min(p.value for p in self.points)

    @property
    def maximum(self) -> float:
        return max(p.value for p in self.points)

    @property
    def average(self) -> float:
        return sum(p.value for p in self.points) / len(self.points)


@dataclass(frozen=True)
class _LogSpec:
    minutes_ago: int
    level: LogLevel
    message: str


@dataclass(frozen=True)
class _MetricSpec:
    baseline: float
    anomaly_value: float | None
    anomaly_started_minutes_ago: int | None

    def value_at(self, minutes_ago: int) -> float:
        """Value `minutes_ago` minutes before now (0 means the latest value)."""
        if (
            self.anomaly_value is not None
            and self.anomaly_started_minutes_ago is not None
            and minutes_ago < self.anomaly_started_minutes_ago
        ):
            return self.anomaly_value
        return self.baseline


@dataclass(frozen=True)
class _ServiceSpec:
    name: str
    status: ServiceStatus
    version: str
    logs: tuple[_LogSpec, ...]
    metrics: Mapping[Metric, _MetricSpec]


def _parse_metric(raw: Mapping[str, Any]) -> _MetricSpec:
    anomaly = raw.get("anomaly")
    return _MetricSpec(
        baseline=float(raw["baseline"]),
        anomaly_value=float(anomaly["value"]) if anomaly else None,
        anomaly_started_minutes_ago=(
            int(anomaly["started_minutes_ago"]) if anomaly else None
        ),
    )


def _parse_service(name: str, raw: Mapping[str, Any]) -> _ServiceSpec:
    logs = tuple(
        _LogSpec(
            minutes_ago=int(entry["minutes_ago"]),
            level=LogLevel(entry["level"]),
            message=str(entry["message"]),
        )
        for entry in raw["logs"]
    )
    metrics = {
        Metric(key): _parse_metric(value) for key, value in raw["metrics"].items()
    }
    missing = sorted(m.value for m in set(Metric) - set(metrics))
    if missing:
        raise ValueError(f"service {name!r} is missing metrics: {', '.join(missing)}")
    return _ServiceSpec(
        name=name,
        status=ServiceStatus(raw["status"]),
        version=str(raw["version"]),
        logs=logs,
        metrics=metrics,
    )


class Fleet:
    """A fixed, deterministic set of simulated services."""

    def __init__(self, now: datetime, services: Mapping[str, _ServiceSpec]) -> None:
        self._now = now
        self._services = dict(services)

    # -- construction -------------------------------------------------

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Fleet:
        try:
            now = datetime.fromisoformat(data["now"])
            services = {
                name: _parse_service(name, raw)
                for name, raw in data["services"].items()
            }
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ScenarioError(f"Invalid scenario: {exc!r}") from exc
        if not services:
            raise ScenarioError("Scenario defines no services")
        return cls(now=now, services=services)

    @classmethod
    def from_file(cls, path: Path) -> Fleet:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ScenarioError(f"Scenario file is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    @classmethod
    def load_default(cls) -> Fleet:
        resource = resources.files("ai_agent_toolbox.simulation").joinpath(
            "scenarios/default.json"
        )
        return cls.from_dict(json.loads(resource.read_text(encoding="utf-8")))

    # -- queries ------------------------------------------------------

    def service_names(self) -> list[str]:
        return sorted(self._services)

    def _get(self, service: str) -> _ServiceSpec:
        try:
            return self._services[service]
        except KeyError:
            raise UnknownServiceError(service, self.service_names()) from None

    def get_status(self, service: str) -> StatusReport:
        spec = self._get(service)
        return StatusReport(
            service=spec.name,
            status=spec.status,
            version=spec.version,
            last_checked=self._now,
        )

    def get_logs(
        self,
        service: str,
        minutes: int,
        level: LogLevel | None = None,
        limit: int = 20,
    ) -> list[LogEntry]:
        """Log entries from the last `minutes` minutes, newest first."""
        if minutes < 1 or limit < 1:
            raise ValueError("minutes and limit must be positive")
        spec = self._get(service)
        selected = [
            entry
            for entry in sorted(spec.logs, key=lambda e: e.minutes_ago)
            if entry.minutes_ago <= minutes and (level is None or entry.level == level)
        ]
        return [
            LogEntry(
                timestamp=self._now - timedelta(minutes=entry.minutes_ago),
                level=entry.level,
                message=entry.message,
            )
            for entry in selected[:limit]
        ]

    def get_metric(self, service: str, metric: Metric, minutes: int) -> MetricSeries:
        """Per-minute values for the last `minutes` minutes, oldest first."""
        if minutes < 1:
            raise ValueError("minutes must be positive")
        spec = self._get(service)
        metric_spec = spec.metrics[metric]
        points = tuple(
            MetricPoint(
                timestamp=self._now - timedelta(minutes=minutes_ago),
                value=metric_spec.value_at(minutes_ago),
            )
            for minutes_ago in range(minutes - 1, -1, -1)
        )
        return MetricSeries(service=spec.name, metric=metric, points=points)
