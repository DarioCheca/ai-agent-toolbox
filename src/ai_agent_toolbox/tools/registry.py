"""Tool registry: validation, execution and output limits.

The registry is the single place where model-supplied input meets tool code.
Mistakes made by the model (unknown tool, invalid arguments, unknown service)
never raise: they come back as structured errors the model can recover from.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from ai_agent_toolbox.simulation import SimulationError

DEFAULT_MAX_OUTPUT_CHARS = 4000
_MIN_MAX_OUTPUT_CHARS = 100
_MAX_REPORTED_VALIDATION_ERRORS = 5


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class ToolResult:
    """Outcome of one tool call.

    `content` is what the model sees. `exception` is kept only for traces and
    is never sent to the model.
    """

    tool: str
    ok: bool
    content: str
    truncated: bool = False
    exception: Exception | None = None


def _dump(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


def _error(
    tool: str,
    error_type: str,
    message: str,
    exception: Exception | None = None,
) -> ToolResult:
    content = _dump({"error": {"type": error_type, "message": message}})
    return ToolResult(tool=tool, ok=False, content=content, exception=exception)


def _summarize_validation_error(exc: ValidationError) -> str:
    """Describe what is wrong without echoing the (untrusted) input back."""
    problems = []
    for error in exc.errors()[:_MAX_REPORTED_VALIDATION_ERRORS]:
        location = ".".join(str(part) for part in error["loc"]) or "input"
        problems.append(f"{location}: {error['msg']}")
    return "; ".join(problems)


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    marker = f"...[truncated: output exceeded {limit} characters]"
    keep = max(limit - len(marker), 0)
    return text[:keep] + marker, True


class ToolRegistry:
    def __init__(
        self,
        tools: Iterable[Tool],
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    ) -> None:
        if max_output_chars < _MIN_MAX_OUTPUT_CHARS:
            raise ValueError(
                f"max_output_chars must be at least {_MIN_MAX_OUTPUT_CHARS}"
            )
        self._max_output_chars = max_output_chars
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool name: {tool.name!r}")
            self._tools[tool.name] = tool

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        """Tool definitions in the format expected by the Anthropic Messages API."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

    def execute(self, name: str, raw_input: object) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return _error(
                name,
                "unknown_tool",
                f"Unknown tool {name!r}. Valid tools: {', '.join(self.names())}",
            )

        try:
            arguments = tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            return _error(
                name, "invalid_arguments", _summarize_validation_error(exc), exc
            )

        try:
            data = tool.handler(arguments)
        except SimulationError as exc:
            return _error(name, "tool_error", str(exc), exc)
        except Exception as exc:  # noqa: BLE001 - a tool bug must not crash the agent
            return _error(name, "internal_error", "The tool failed unexpectedly.", exc)

        content, truncated = _truncate(_dump(data), self._max_output_chars)
        return ToolResult(tool=name, ok=True, content=content, truncated=truncated)
