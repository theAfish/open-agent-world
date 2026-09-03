from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Sequence

if TYPE_CHECKING:
    from backend.world.models import Card, CardCreate, CardPatch, Edge


class NodeLifecycleNodes(Protocol):
    """Narrow world access available to node lifecycle handlers."""

    def update_status(self, node_id: str, status: str) -> Card: ...

    def list_edges_from(self, node_id: str) -> Sequence[Edge]: ...


class AgentNodeLifecycle(Protocol):
    """Provider-neutral lifecycle operations for an agent-backed node."""

    async def create(self, node: Card) -> None: ...

    async def update(self, node: Card) -> None: ...

    async def delete(self, node_id: str, *, missing_ok: bool = False) -> None: ...

    async def stop(self, node_id: str) -> None: ...


class SandboxNodeLifecycle(Protocol):
    """Provider-neutral lifecycle operations for an execution environment."""

    async def ensure(self, node_id: str) -> str: ...

    async def create(self, node_id: str) -> None: ...

    async def destroy(self, node_id: str, *, missing_ok: bool = False) -> None: ...

    async def terminate(self, node_id: str, *, missing_ok: bool = False) -> None: ...

    async def detach_resource(
        self, node_id: str, resource_id: str, *, missing_ok: bool = False
    ) -> None: ...


class ManagedResourceLifecycle(Protocol):
    """Managed-file operations needed while creating and deleting resource nodes."""

    def create_text(
        self, node_id: str, filename: str, content: str = ""
    ) -> None: ...

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None: ...

    def remove_file(self, node_id: str) -> None: ...


class ConversationNodeLifecycle(Protocol):
    """Conversation operations needed during node creation."""

    def create_initial_session(self, node_id: str, title: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NodeLifecycleContext:
    """Small service surface shared by built-in and third-party node behaviors.

    Provider SDKs, HTTP objects, database connections, and the application
    service container intentionally do not cross this boundary.
    """

    nodes: NodeLifecycleNodes
    resources: ManagedResourceLifecycle
    conversations: ConversationNodeLifecycle
    agents: AgentNodeLifecycle | None = None
    sandboxes: SandboxNodeLifecycle | None = None


class NodeLifecycleHandler:
    """Optional lifecycle callbacks for one registered node type.

    ``on_create_rollback`` is invoked whenever ``on_create`` raises, including
    cancellation. Implementations must use it to undo any partial side effects
    created before the failure. The persisted node is removed by the service
    layer after this callback returns (or raises).
    """

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        pass

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        pass

    async def on_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> None:
        pass

    async def on_create_rollback(
        self,
        context: NodeLifecycleContext,
        node: Card,
        request: CardCreate,
        error: BaseException,
    ) -> None:
        pass

    async def on_update(
        self, context: NodeLifecycleContext, node: Card, request: CardPatch
    ) -> None:
        pass

    async def on_delete(self, context: NodeLifecycleContext, node: Card) -> None:
        pass
