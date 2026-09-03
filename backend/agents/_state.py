"""Small runtime-internal state helpers shared by all agent runtimes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
import asyncio

from .models import (
    AgentConfig,
    AgentConfigurationError,
    AgentInfo,
    AgentStatus,
)


_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(slots=True)
class AgentRecord:
    config: AgentConfig
    session_id: str
    status: AgentStatus = AgentStatus.IDLE
    last_error: str | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def info(self) -> AgentInfo:
        return AgentInfo(
            config=self.config,
            status=self.status,
            session_id=self.session_id,
            active_run_id=None,
            last_error=self.last_error,
        )


def validate_agent_config(config: AgentConfig) -> None:
    if not isinstance(config, AgentConfig):
        raise AgentConfigurationError("config must be an AgentConfig")
    if _SAFE_AGENT_ID.fullmatch(config.agent_id) is None:
        raise AgentConfigurationError(f"invalid agent_id: {config.agent_id!r}")
    if not config.name.strip() or len(config.name) > 200:
        raise AgentConfigurationError("agent name must be non-empty and bounded")
    if not config.model.strip() or len(config.model) > 200:
        raise AgentConfigurationError("agent model must be non-empty and bounded")
    if not config.system_instruction.strip():
        raise AgentConfigurationError("system instruction must not be empty")
    if not 1 <= config.max_concurrent_runs <= 64:
        raise AgentConfigurationError("max_concurrent_runs must be between 1 and 64")
