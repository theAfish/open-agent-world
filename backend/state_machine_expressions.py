"""Typed, bounded expressions for declarative state-machine triggers.

This module evaluates a small algebra, never Python or user-provided code.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


Reference = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"^[^\s\x00]+$")]
SignalId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,31}$")]
MAX_EXPRESSION_DEPTH = 12
MAX_EXPRESSION_NODES = 128


class ExpressionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NumberExpression(ExpressionModel):
    op: Literal["number"]
    value: float = Field(ge=-1e12, le=1e12, allow_inf_nan=False)


class CountExpression(ExpressionModel):
    op: Literal["count"]
    signal: SignalId


class EventExpression(ExpressionModel):
    op: Literal["event"]
    signal: SignalId


class StateExpression(ExpressionModel):
    op: Literal["state"]
    entity_id: Reference
    state_id: Reference


class ArithmeticExpression(ExpressionModel):
    op: Literal["add", "sub", "mul", "div", "mod"]
    left: "Expression"
    right: "Expression"


class ComparisonExpression(ExpressionModel):
    op: Literal["eq", "ne", "gt", "gte", "lt", "lte"]
    left: "Expression"
    right: "Expression"


class LogicalExpression(ExpressionModel):
    op: Literal["all", "any"]
    args: list["Expression"] = Field(min_length=1, max_length=64)


class NotExpression(ExpressionModel):
    op: Literal["not"]
    arg: "Expression"


Expression = Annotated[
    NumberExpression | CountExpression | EventExpression | StateExpression
    | ArithmeticExpression | ComparisonExpression | LogicalExpression | NotExpression,
    Field(discriminator="op"),
]
for _model in (ArithmeticExpression, ComparisonExpression, LogicalExpression, NotExpression):
    _model.model_rebuild()


def bound_expression(value: Any) -> Any:
    """Reject excessive input before Pydantic recursively builds the AST."""
    pending, count = [(value, 1)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if depth > MAX_EXPRESSION_DEPTH or count > MAX_EXPRESSION_NODES:
            raise ValueError("trigger expressions support at most 128 nodes and 12 levels")
        if isinstance(node, BaseModel):
            node = node.model_dump()
        if not isinstance(node, dict):
            continue  # The discriminated model supplies the precise type error.
        if node.get("op") in {"add", "sub", "mul", "div", "mod", "eq", "ne", "gt", "gte", "lt", "lte"}:
            pending.extend((node.get(key), depth + 1) for key in ("left", "right"))
        elif node.get("op") in {"all", "any"} and isinstance(node.get("args"), list):
            pending.extend((child, depth + 1) for child in node["args"])
        elif node.get("op") == "not":
            pending.append((node.get("arg"), depth + 1))
    return value


def expression_type(expression: Expression) -> Literal["number", "boolean"]:
    if isinstance(expression, (NumberExpression, CountExpression)):
        return "number"
    if isinstance(expression, (EventExpression, StateExpression)):
        return "boolean"
    if isinstance(expression, (ArithmeticExpression, ComparisonExpression)):
        if expression_type(expression.left) != "number" or expression_type(expression.right) != "number":
            raise ValueError(f"{expression.op} requires two numeric operands")
        return "number" if isinstance(expression, ArithmeticExpression) else "boolean"
    children = expression.args if isinstance(expression, LogicalExpression) else [expression.arg]
    if any(expression_type(child) != "boolean" for child in children):
        raise ValueError(f"{expression.op} requires boolean operands")
    return "boolean"


def expression_nodes(expression: Expression):
    yield expression
    if isinstance(expression, (ArithmeticExpression, ComparisonExpression)):
        yield from expression_nodes(expression.left)
        yield from expression_nodes(expression.right)
    elif isinstance(expression, LogicalExpression):
        for child in expression.args:
            yield from expression_nodes(child)
    elif isinstance(expression, NotExpression):
        yield from expression_nodes(expression.arg)


class ExpressionEvaluationError(ValueError):
    pass


def evaluate_expression(expression: Expression, counts: dict[str, int], matched: set[str], states: dict[str, str]) -> bool | float:
    if isinstance(expression, NumberExpression):
        return expression.value
    if isinstance(expression, CountExpression):
        return counts[expression.signal]
    if isinstance(expression, EventExpression):
        return expression.signal in matched
    if isinstance(expression, StateExpression):
        return states.get(expression.entity_id) == expression.state_id
    if isinstance(expression, NotExpression):
        return not evaluate_expression(expression.arg, counts, matched, states)
    if isinstance(expression, LogicalExpression):
        values = (evaluate_expression(child, counts, matched, states) for child in expression.args)
        return all(values) if expression.op == "all" else any(values)
    left = evaluate_expression(expression.left, counts, matched, states)
    right = evaluate_expression(expression.right, counts, matched, states)
    if isinstance(expression, ComparisonExpression):
        return {"eq": lambda: left == right, "ne": lambda: left != right,
                "gt": lambda: left > right, "gte": lambda: left >= right,
                "lt": lambda: left < right, "lte": lambda: left <= right}[expression.op]()
    if expression.op in {"div", "mod"} and right == 0:
        raise ExpressionEvaluationError("division_by_zero" if expression.op == "div" else "modulo_by_zero")
    result = {"add": lambda: left + right, "sub": lambda: left - right,
              "mul": lambda: left * right, "div": lambda: left / right,
              # Match JavaScript remainder semantics, including negative values.
              "mod": lambda: math.fmod(left, right)}[expression.op]()
    if not math.isfinite(result):
        raise ExpressionEvaluationError("non_finite_result")
    return result
