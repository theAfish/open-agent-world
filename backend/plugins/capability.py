from __future__ import annotations

from typing import Any, Protocol


class CapabilityContext(Protocol):
    """Narrow host operations available to trusted capability handlers."""

    async def communicate(
        self, source_agent_id: str, target_agent_id: str, message: str
    ) -> Any: ...

    async def request_conversation_turn(
        self,
        source_agent_id: str,
        conversation_id: str,
        participant_agent_id: str,
        message: str,
    ) -> Any: ...

    def read_text(self, agent_id: str, resource_id: str) -> dict[str, Any]: ...

    async def replace_text(
        self, agent_id: str, resource_id: str, content: str
    ) -> dict[str, Any]: ...

    def view_image(self, agent_id: str, resource_id: str) -> dict[str, Any]: ...

    async def execute_sandbox(
        self, agent_id: str, sandbox_id: str, argv: list[str]
    ) -> dict[str, Any]: ...
