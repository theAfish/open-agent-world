from __future__ import annotations

from typing import Any, Protocol


class CapabilityContext(Protocol):
    """Narrow host operations available to trusted capability handlers."""

    async def legion_state_action(self, capability: Any, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def summoning_action(self, capability: Any, arguments: dict[str, Any]) -> dict[str, Any]: ...

    async def node_document_action(self, capability: Any, action: str, arguments: dict[str, Any], expected_revision: int | None = None) -> dict[str, Any]: ...

    async def node_execution_action(self, capability: Any, action: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

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
        self, agent_id: str, sandbox_id: str, argv: list[str], *, environment_id: str | None = None, target_id: str | None = None
    ) -> dict[str, Any]: ...

    async def inspect_sandbox(self, agent_id: str, sandbox_id: str) -> dict[str, Any]: ...

    async def run_skill_script(
        self, agent_id: str, sandbox_id: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...
