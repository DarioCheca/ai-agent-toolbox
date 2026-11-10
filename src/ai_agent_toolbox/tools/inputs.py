"""Input models for the agent's tools.

These models are the trust boundary: everything the model sends to a tool is
validated here before any tool code runs. Validation is strict (no silent
coercion, no unknown fields) and every numeric argument has hard bounds.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Literal types (instead of Enums) keep the generated JSON schema flat and
# readable for the model. Tests assert they stay in sync with the simulation.
LogLevelName = Literal["DEBUG", "INFO", "WARN", "ERROR"]
MetricName = Literal[
    "latency_p95_ms",
    "error_rate",
    "cpu_percent",
    "memory_percent",
    "request_rate",
]

ServiceName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_-]*$",
        description="Name of the service in lowercase, for example 'payments'.",
    ),
]


class _ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetServiceStatusInput(_ToolInput):
    service: ServiceName


class GetRecentLogsInput(_ToolInput):
    service: ServiceName
    minutes: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            strict=True,
            description="How many minutes back to look (1-60).",
        ),
    ] = 15
    level: LogLevelName | None = Field(
        default=None,
        description="Only return entries with exactly this level.",
    )
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=50,
            strict=True,
            description="Maximum number of entries to return (1-50).",
        ),
    ] = 20


class GetMetricsInput(_ToolInput):
    service: ServiceName
    metric: MetricName = Field(description="The metric to read.")
    minutes: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            strict=True,
            description="How many minutes back to look (1-60).",
        ),
    ] = 15
