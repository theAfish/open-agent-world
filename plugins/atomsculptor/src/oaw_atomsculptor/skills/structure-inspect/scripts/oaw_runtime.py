"""Portable command helpers for AtomSculptor skills running in OAW.

OAW invokes a Skill from a read-only materialization while the process working
directory is the separately authorized Sandbox workspace.  This module does
not discover interpreters, create environments, or access host settings.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from types import NoneType, UnionType
from typing import Any, Callable, Union, get_args, get_origin


def workspace_root() -> Path:
    return Path.cwd().resolve()


def workspace_output_dir() -> Path:
    return workspace_root()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace_root()))
    except ValueError:
        return str(path.resolve())


def resolve_output_path(output_name: str) -> Path:
    candidate = Path(output_name)
    if candidate.is_absolute():
        raise ValueError("outputs must be paths relative to the OAW Sandbox workspace")
    resolved = (workspace_output_dir() / candidate).resolve()
    try:
        resolved.relative_to(workspace_root())
    except ValueError as exc:
        raise ValueError("outputs must remain inside the OAW Sandbox workspace") from exc
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def annotation_to_cli_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if annotation in (inspect._empty, Any, str):
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    if annotation in (list, tuple, dict) or origin in (list, tuple, dict):
        return "JSON"
    if origin in (UnionType, Union):
        parts = [item for item in get_args(annotation) if item is not NoneType]
        if len(parts) == 1:
            return annotation_to_cli_type(parts[0])
    return "string"


def _coerce(value: str, annotation: Any) -> Any:
    origin = get_origin(annotation)
    if annotation in (inspect._empty, Any, str):
        return value
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "t", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "f", "no", "n", "off"}:
            return False
        raise ValueError(f"Expected a boolean value, got: {value!r}")
    if annotation in (list, tuple, dict) or origin in (list, tuple, dict):
        decoded = json.loads(value)
        expected = origin or annotation
        if not isinstance(decoded, expected if expected is not tuple else list):
            raise ValueError(f"Expected JSON {expected.__name__}, got: {value}")
        return tuple(decoded) if expected is tuple else decoded
    if origin in (UnionType, Union):
        errors = []
        for option in (item for item in get_args(annotation) if item is not NoneType):
            try:
                return _coerce(value, option)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))
        raise ValueError(errors[-1] if errors else f"Cannot parse {value!r}")
    return value


def build_cli_parser(*, prog: str, description_lines: list[str], tool_functions: dict[str, Callable[..., Any]]) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="\n".join(description_lines), formatter_class=argparse.RawTextHelpFormatter)
    commands = parser.add_subparsers(dest="tool_name", metavar="tool_name")
    for name, function in tool_functions.items():
        command = commands.add_parser(name, help=(inspect.getdoc(function) or name).splitlines()[0])
        for parameter in inspect.signature(function).parameters.values():
            command.add_argument(
                f"--{parameter.name.replace('_', '-')}",
                dest=parameter.name,
                required=parameter.default is inspect._empty,
                help=f"({annotation_to_cli_type(parameter.annotation)})",
            )
    return parser


def run_cli(*, argv: list[str] | None, parser: argparse.ArgumentParser, tool_functions: dict[str, Callable[..., Any]]) -> int:
    arguments = parser.parse_args(argv)
    if not arguments.tool_name:
        parser.print_help()
        return 0
    function = tool_functions[arguments.tool_name]
    try:
        values = {
            parameter.name: _coerce(getattr(arguments, parameter.name), parameter.annotation)
            for parameter in inspect.signature(function).parameters.values()
            if getattr(arguments, parameter.name) is not None
        }
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    result = function(**values)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if isinstance(result, dict) and "error" in result else 0
