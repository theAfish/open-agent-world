"""Shared execution boundary for model-facing plugin tool adapters."""
from backend.agents.tool_execution import ToolOutcome, execute_tool

__all__ = ["ToolOutcome", "execute_tool"]
