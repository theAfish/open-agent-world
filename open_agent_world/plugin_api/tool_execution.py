"""Shared execution boundary for model-facing plugin tool adapters."""
from backend.agents.tool_execution import (
    ToolOutcome, execute_tool, tool_payload, collect_operation_result, OperationReceiptMismatch,
)

__all__ = ["ToolOutcome", "execute_tool", "tool_payload", "collect_operation_result", "OperationReceiptMismatch"]
