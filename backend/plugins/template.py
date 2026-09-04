from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol

from backend.errors import PluginCompatibilityError
from backend.plugins.lifecycle import NodeLifecycleTransaction

if TYPE_CHECKING:
    from backend.world.models import Card


NodeTemplateDependencyKind = Literal[
    "node_type",
    "relationship",
    "capability_handler",
    "runtime_provider",
    "state_schema",
]


@dataclass(frozen=True, slots=True)
class NodeTemplateDependency:
    """One registry contribution required to restore a portable node."""

    kind: NodeTemplateDependencyKind
    id: str


@dataclass(frozen=True, slots=True)
class NodeTemplateBinary:
    """Portable binary content returned through the narrow template boundary."""

    filename: str
    media_type: str
    data_base64: str


class NodeTemplateCaptureResources(Protocol):
    """Read-only managed resources exposed while capturing a source node."""

    def read_text(self, node_id: str) -> str: ...

    def read_binary(self, node_id: str) -> NodeTemplateBinary | None: ...


class NodeTemplateRestoreResources(Protocol):
    """Managed-resource writes available while restoring a new node."""

    def replace_text(self, node_id: str, content: str) -> None: ...

    def create_image(
        self, node_id: str, filename: str, media_type: str, data_base64: str
    ) -> None: ...

    def remove_file(self, node_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class NodeTemplateCaptureContext:
    """Provider-neutral, read-only host operations used during capture."""

    resources: NodeTemplateCaptureResources


@dataclass(frozen=True, slots=True)
class NodeTemplateRestoreContext:
    """Provider-neutral host operations used to populate a newly created node."""

    resources: NodeTemplateRestoreResources


class NodeTemplateHandler:
    """Captures and restores one node type's portable sidecar state.

    The host always snapshots the normal card fields and validates them through
    the registered node definition. A handler is needed to project portable
    configuration (especially to exclude credentials or machine-local fields),
    to capture durable state outside ``Card.config``, or both. ``capture`` must
    be side-effect free and deterministic for unchanged state because the host
    invokes it twice to detect concurrent mutation. ``payload_version`` versions
    the handler-owned payload independently from the plugin package version.
    """

    payload_version = 1

    def dependencies(
        self, config: Mapping[str, Any]
    ) -> tuple[NodeTemplateDependency, ...]:
        """Declare registry contributions required by this portable config.

        The host resolves and persists each contribution's owning plugin when
        the Legion is captured. Restore then fails closed if a contribution is
        missing or has moved to another owner.
        """

        del config
        return ()

    def capture_config(self, node: Card) -> dict[str, Any]:
        """Project the Card configuration into its portable representation.

        The default copies the complete validated configuration. Nodes with
        machine-local fields or credentials must override this projection (or
        remain non-templateable) so those values never enter a Legion.
        """

        return dict(node.config)

    def supports_payload_version(self, payload_version: int) -> bool:
        """Return whether this handler can restore the stored payload version."""

        return payload_version == self.payload_version

    def validate_payload(
        self, payload: Mapping[str, Any], payload_version: int
    ) -> None:
        """Validate handler-owned portable state without mutating host state."""

        del payload
        if not self.supports_payload_version(payload_version):
            raise PluginCompatibilityError(
                f"template payload version {payload_version!r} is unsupported"
            )

    async def capture(
        self,
        context: NodeTemplateCaptureContext,
        node: Card,
        node_keys: Mapping[str, str],
    ) -> dict[str, Any]:
        del context, node, node_keys
        return {}

    async def prepare_restore(
        self,
        context: NodeTemplateRestoreContext,
        node: Card,
        payload: Mapping[str, Any],
        payload_version: int,
        node_ids: Mapping[str, str],
    ) -> NodeLifecycleTransaction:
        self.validate_payload(payload, payload_version)
        del context, node, payload, node_ids
        return NodeLifecycleTransaction()
