"""Explicit runtime selection; failures never downgrade to the mock."""

from __future__ import annotations

from typing import Any, Literal

from .base import AgentCapabilityProvider, AgentRuntime
from .google_adk import GoogleAdkAgentRuntime
from .litellm import LiteLLMAgentRuntime
from .mock import MockAgentRuntime
from .models import AgentConfigurationError


AgentRuntimeKind = Literal["google-adk", "litellm", "mock"]


def create_agent_runtime(
    kind: AgentRuntimeKind | str,
    capability_provider: AgentCapabilityProvider,
    **kwargs: Any,
) -> AgentRuntime:
    """Construct exactly the configured runtime.

    ``kind`` has no default so development mock use is always visible in
    application configuration.  Import/auth/model failures from ADK propagate.
    """

    if kind == "google-adk":
        return GoogleAdkAgentRuntime(capability_provider, **kwargs)
    if kind == "litellm":
        return LiteLLMAgentRuntime(capability_provider, **kwargs)
    if kind == "mock":
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise AgentConfigurationError(f"mock runtime options are invalid: {unknown}")
        return MockAgentRuntime(capability_provider)
    raise AgentConfigurationError(f"unknown agent runtime: {kind!r}")
