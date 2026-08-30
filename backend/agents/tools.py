"""Construction of ADK-compatible callables from scoped capabilities."""

from __future__ import annotations

import inspect
import keyword
import re
from collections.abc import Callable, Sequence
from typing import Any

from .base import AgentCapabilityProvider
from .models import AgentConfigurationError, ScopedToolDefinition


_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def build_scoped_tool_callables(
    provider: AgentCapabilityProvider,
    agent_id: str,
    definitions: Sequence[ScopedToolDefinition],
) -> list[Callable[..., Any]]:
    """Build plain async functions for ADK's automatic ``FunctionTool`` wrap.

    The callable closes over only the agent and capability identities.  It does
    not close over a resource path or privileged object; execution always goes
    back through ``provider.invoke_tool`` for a current authorization check.
    """

    names: set[str] = set()
    capability_ids: set[str] = set()
    callables: list[Callable[..., Any]] = []
    for definition in definitions:
        _validate_definition(definition)
        if definition.name in names:
            raise AgentConfigurationError(f"duplicate tool name: {definition.name}")
        if definition.capability_id in capability_ids:
            raise AgentConfigurationError(
                f"duplicate capability id: {definition.capability_id}"
            )
        names.add(definition.name)
        capability_ids.add(definition.capability_id)
        callables.append(_build_callable(provider, agent_id, definition))
    return callables


def _build_callable(
    provider: AgentCapabilityProvider,
    agent_id: str,
    definition: ScopedToolDefinition,
) -> Callable[..., Any]:
    async def scoped_tool(**arguments: Any) -> Any:
        return await provider.invoke_tool(
            agent_id, definition.capability_id, dict(arguments)
        )

    scoped_tool.__name__ = definition.name
    scoped_tool.__qualname__ = definition.name
    scoped_tool.__doc__ = _docstring(definition)
    parameters = []
    for parameter in definition.parameters:
        default = inspect.Parameter.empty if parameter.required else parameter.default
        parameters.append(
            inspect.Parameter(
                parameter.name,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=parameter.python_type,
            )
        )
    scoped_tool.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
        parameters=parameters,
        return_annotation=Any,
    )
    return scoped_tool


def _validate_definition(definition: ScopedToolDefinition) -> None:
    if not definition.capability_id or len(definition.capability_id) > 256:
        raise AgentConfigurationError("capability_id must be non-empty and bounded")
    if _TOOL_NAME.fullmatch(definition.name) is None:
        raise AgentConfigurationError(f"invalid ADK tool name: {definition.name!r}")
    if not definition.description.strip():
        raise AgentConfigurationError(f"tool {definition.name} needs a description")
    parameter_names: set[str] = set()
    optional_seen = False
    for parameter in definition.parameters:
        if (
            _TOOL_NAME.fullmatch(parameter.name) is None
            or keyword.iskeyword(parameter.name)
        ):
            raise AgentConfigurationError(
                f"invalid tool parameter name: {parameter.name!r}"
            )
        if parameter.name in parameter_names:
            raise AgentConfigurationError(
                f"duplicate parameter {parameter.name!r} in {definition.name}"
            )
        if not isinstance(parameter.python_type, type):
            raise AgentConfigurationError(
                f"parameter {parameter.name!r} needs a concrete Python type"
            )
        # inspect.Signature also enforces this, but this error is clearer.
        if not parameter.required:
            optional_seen = True
        elif optional_seen:
            raise AgentConfigurationError(
                "required parameters must precede optional parameters"
            )
        parameter_names.add(parameter.name)


def _docstring(definition: ScopedToolDefinition) -> str:
    if not definition.parameters:
        return definition.description.strip()
    lines = [definition.description.strip(), "", "Args:"]
    for parameter in definition.parameters:
        description = parameter.description.strip() or "Tool argument."
        lines.append(f"    {parameter.name}: {description}")
    return "\n".join(lines)

