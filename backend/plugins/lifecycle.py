from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

if TYPE_CHECKING:
    from backend.world.models import Card, CardCreate, CardPatch, Edge


class NodeLifecycleNodes(Protocol):
    """Narrow world access available to node lifecycle handlers."""

    def update_status(self, node_id: str, status: str) -> Card: ...

    def list_edges_from(self, node_id: str) -> Sequence[Edge]: ...


class AgentNodeLifecycle(Protocol):
    async def reserve_delete(self, node_id: str) -> None: ...

    def release_delete(self, node_id: str) -> None: ...

    async def create(self, node: Card) -> None: ...

    async def update(self, node: Card) -> None: ...

    async def delete(
        self,
        node_id: str,
        *,
        missing_ok: bool = False,
        provider_id: str | None = None,
    ) -> None: ...

    def provider_id(self, node: Card) -> str | None: ...

    async def stop(self, node_id: str) -> None: ...


class SandboxNodeLifecycle(Protocol):
    async def ensure(self, node_id: str) -> str: ...

    async def create(self, node_id: str) -> None: ...

    async def configure(self, node_id: str, config: Mapping[str, Any]) -> None: ...

    async def start(self, node_id: str) -> None: ...

    async def destroy(self, node_id: str, *, missing_ok: bool = False) -> None: ...

    async def terminate(self, node_id: str, *, missing_ok: bool = False) -> None: ...

    async def detach_resource(
        self, node_id: str, resource_id: str, *, missing_ok: bool = False
    ) -> None: ...

    async def attach_resource(
        self,
        node_id: str,
        resource_id: str,
        *,
        writable: bool,
        missing_ok: bool = False,
    ) -> None: ...


class ManagedResourceRemoval(Protocol):
    """A prepared file removal that can restore its exact prior bytes."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


class ManagedResourceLifecycle(Protocol):
    def create_text(self, node_id: str, filename: str, content: str = "") -> None: ...

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None: ...

    def remove_file(self, node_id: str) -> None: ...

    def prepare_file_removal(self, node_id: str) -> ManagedResourceRemoval | None: ...


class ConversationNodeLifecycle(Protocol):
    def create_initial_session(self, node_id: str, title: str) -> None: ...

    def delete_session_state(self, node_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NodeLifecycleContext:
    """Provider-neutral host operations available to node lifecycle code."""

    nodes: NodeLifecycleNodes
    resources: ManagedResourceLifecycle
    conversations: ConversationNodeLifecycle
    agents: AgentNodeLifecycle | None = None
    sandboxes: SandboxNodeLifecycle | None = None


class NodeLifecycleTransaction:
    """One prepared, reversible plugin-side part of a node mutation.

    ``commit``, ``rollback``, and ``finalize`` must be idempotent. ``rollback``
    may be called after a partial or successful commit and must restore the
    pre-mutation plugin/external state. For a durable delete it may also be
    called after the host records commit intent but before ``commit`` actually
    begins, so an uncommitted rollback must be harmless. Preparation itself must not create
    visible side effects. A delete transaction may name other selected nodes in
    ``commit_before_node_ids`` when it must commit before their transactions;
    the host reverses that order during compensation. ``finalize`` runs only
    after the authoritative world mutation commits, may be retried after a
    transient failure, and therefore must not mutate the world graph.
    """

    commit_before_node_ids: frozenset[str] = frozenset()

    @property
    def has_delete_finalizer(self) -> bool:
        """Whether deletion needs durable post-commit cleanup."""

        return type(self).finalize is not NodeLifecycleTransaction.finalize

    @property
    def delete_recovery_payload(self) -> Mapping[str, Any]:
        """Serializable inputs needed to replay deletion cleanup."""

        return {}

    async def commit(self) -> None:
        pass

    async def rollback(self, error: BaseException) -> None:
        pass

    async def finalize(self) -> None:
        """Run irreversible cleanup after the world mutation commits."""

        pass


class NodeLifecycleHandler:
    """Prepares reversible transactions for one registered node type."""

    async def on_startup(self, context: NodeLifecycleContext, node: Card) -> None:
        pass

    async def on_shutdown(self, context: NodeLifecycleContext, node: Card) -> None:
        pass

    async def prepare_create(
        self, context: NodeLifecycleContext, node: Card, request: CardCreate
    ) -> NodeLifecycleTransaction:
        return NodeLifecycleTransaction()

    async def prepare_update(
        self,
        context: NodeLifecycleContext,
        current: Card,
        updated: Card,
        request: CardPatch,
    ) -> NodeLifecycleTransaction:
        return NodeLifecycleTransaction()

    async def prepare_delete(
        self, context: NodeLifecycleContext, node: Card
    ) -> NodeLifecycleTransaction:
        return NodeLifecycleTransaction()

    async def prepare_delete_recovery(
        self,
        context: NodeLifecycleContext,
        node: Card,
        *,
        plugin_version: str,
        payload: Mapping[str, Any],
    ) -> NodeLifecycleTransaction:
        """Rebuild a deletion transaction after a restart.

        When the Card is still present, the host invokes ``rollback`` without
        replaying ``commit``. When the graph deletion committed, it invokes
        only ``finalize``. The returned transaction must support both paths;
        plugins can override this method to reconstruct captured external state
        or migrate snapshots created by older plugin versions.
        """

        del plugin_version, payload
        return await self.prepare_delete(context, node)

    def supports_delete_recovery_version(
        self, saved_plugin_version: str, current_plugin_version: str
    ) -> bool:
        """Declare which persisted deletion snapshots this handler can replay."""

        return saved_plugin_version == current_plugin_version
