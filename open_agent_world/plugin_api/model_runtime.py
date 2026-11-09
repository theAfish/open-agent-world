"""Host services for trusted model-runtime adapters.

The host owns Run receipts, checkpoints, observation and capability scoping.
These additive contracts let adapters reuse those services without importing
private backend paths. Optional ADK integrations load only when requested.
"""
from importlib import import_module

_EXPORTS = {
    "build_scoped_adk_tools": ("backend.agents.tools", "build_scoped_adk_tools"),
    "ModelTrace": ("backend.agents.model_observation", "ModelTrace"),
    "model_stream_disconnected": ("backend.agents.model_observation", "model_stream_disconnected"),
    "multiplex_adk_events": ("backend.agents.model_observation", "multiplex_adk_events"),
    "RequestRetryPolicy": ("backend.agents.request_recovery", "RequestRetryPolicy"),
    "snapshot_request": ("backend.agents.request_recovery", "_snapshot"),
    "recoverable_model": ("backend.agents.request_recovery", "recoverable_model"),
    "RequestWindow": ("backend.agents.request_window", "RequestWindow"),
    "signed_history": ("backend.agents.request_window", "signed_history"),
    "ContextCapabilities": ("backend.agents.request_window", "ContextCapabilities"),
    "ManagedContext": ("backend.agents.context", "ManagedContext"),
    "ContextBudget": ("backend.agents.context", "ContextBudget"),
    "archived_tool_callbacks": ("backend.agents.tool_observation", "archived_tool_callbacks"),
    "ResilientLiteLlm": ("backend.agents.resilient_litellm", "ResilientLiteLlm"),
    "archive_value": ("backend.runs.invocation_logs", "archive_value"),
    "RunModelCheckpoints": ("backend.runs.model_checkpoints", "RunModelCheckpoints"),
    "RunToolReceipts": ("backend.runs.tool_receipts", "RunToolReceipts"),
    "recovery_state": ("backend.runs.recovery", "recovery_state"),
    "runtime_source_snapshot": ("backend.runs.provenance", "runtime_source_snapshot"),
}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, member = _EXPORTS[name]
    return getattr(import_module(module), member)
