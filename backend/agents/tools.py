"""Construction of runtime tool definitions from scoped capabilities."""

from __future__ import annotations

import inspect
from copy import deepcopy
import keyword
import re
from collections.abc import Callable, Sequence
from typing import Any

from backend.errors import DomainError

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


def build_scoped_adk_tools(
    provider: AgentCapabilityProvider,
    agent_id: str,
    definitions: Sequence[ScopedToolDefinition],
) -> list[Any]:
    """Expose the complete capability contract to ADK and its model adapter.

    Inferring declarations from the compatibility callables loses nested JSON
    schemas, selector enums and constraints. In particular a Pydantic ``$ref``
    has no direct ``type`` and used to be advertised as a string. Execution
    still delegates to the same callable and its live authorization check.
    """
    from google.adk.tools import FunctionTool
    from google.genai import types

    class ScopedFunctionTool(FunctionTool):
        def __init__(self, function: Callable[..., Any], declaration: dict[str, Any]):
            super().__init__(function)
            self._scoped_declaration = types.FunctionDeclaration(
                name=declaration["name"],
                description=declaration["description"],
                parameters_json_schema=_inline_schema_refs(declaration["parameters"]),
            )

        def _get_declaration(self):
            # ADK adapters and toolsets can mutate declarations while preparing
            # a request. Never let that alter another model turn's contract.
            return self._scoped_declaration.model_copy(deep=True)

    functions = build_scoped_tool_callables(provider, agent_id, definitions)
    schemas = build_scoped_tool_schemas(definitions)
    return [
        ScopedFunctionTool(function, schema["function"])
        for function, schema in zip(functions, schemas, strict=True)
    ]


def _inline_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Expand acyclic local references; retain recursive schemas unchanged.

    Inlining avoids adapters which overlook ``$defs``. Recursive references
    remain valid JSON Schema with their definitions instead of being truncated
    or expanded indefinitely. The registered schema is never mutated.
    """
    root = deepcopy(schema)

    class PreserveRefs(Exception):
        pass

    def visit(value: Any, ancestors: frozenset[str] = frozenset()) -> Any:
        if isinstance(value, list):
            return [visit(item, ancestors) for item in value]
        if not isinstance(value, dict):
            return value
        reference = value.get("$ref")
        if isinstance(reference, str) and not reference.startswith("#/"):
            # Remote/anchor references need their original resolution scope.
            raise PreserveRefs
        if isinstance(reference, str) and reference.startswith("#/"):
            if reference in ancestors:
                raise RecursionError
            target: Any = root
            try:
                for part in reference[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                raise AgentConfigurationError(f"unresolved tool schema reference: {reference}") from exc
            expanded = visit(target, ancestors | {reference})
            siblings = {key: visit(item, ancestors) for key, item in value.items() if key not in {"$ref", "$defs", "definitions"}}
            # Preserve conjunctive constraints when a reference has siblings.
            if any(key in expanded and key not in {"title", "description", "default"} for key in siblings):
                return {"allOf": [expanded, siblings]}
            return {**expanded, **siblings}
        return {key: visit(item, ancestors) for key, item in value.items() if key not in {"$defs", "definitions"}}

    try:
        return visit(root)
    except (RecursionError, PreserveRefs):
        return root


def build_scoped_tool_schemas(
    definitions: Sequence[ScopedToolDefinition],
) -> list[dict[str, Any]]:
    """Build JSON function-tool schemas from scoped capabilities.

    The same validation rules are used for ADK callables and Chat Completions
    tools, so alternate representations cannot bypass the capability boundary.
    """

    names: set[str] = set()
    capability_ids: set[str] = set()
    schemas: list[dict[str, Any]] = []
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
        properties = {
            parameter.name: {
                "type": _json_schema_type(parameter.python_type),
                "description": parameter.description.strip() or "Tool argument.",
            }
            for parameter in definition.parameters
        }
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description.strip(),
                    "parameters": deepcopy(dict(definition.input_schema)) if definition.input_schema is not None else {
                        "type": "object",
                        "properties": properties,
                        "required": [
                            parameter.name
                            for parameter in definition.parameters
                            if parameter.required
                        ],
                        "additionalProperties": False,
                    },
                },
            }
        )
    return schemas


def _json_schema_type(python_type: type[Any]) -> str:
    if python_type is bool:
        return "boolean"
    if python_type is int:
        return "integer"
    if python_type is float:
        return "number"
    if python_type is list:
        return "array"
    if python_type is dict:
        return "object"
    return "string"


def _build_callable(
    provider: AgentCapabilityProvider,
    agent_id: str,
    definition: ScopedToolDefinition,
) -> Callable[..., Any]:
    async def scoped_tool(**arguments: Any) -> Any:
        try:
            result = await provider.invoke_tool(
                agent_id, definition.capability_id, dict(arguments)
            )
            from .media import adk_tool_result
            return adk_tool_result(result)
        except DomainError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "type": type(exc).__name__,
                    "message": exc.message,
                },
            }

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
