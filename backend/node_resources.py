"""Dispatch native resource operations through live graph authorization."""
import asyncio
import json
from threading import Event

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.node_documents import validation_message
from backend.plugins.resources import NodeResourceAction, NodeResourceContext
from backend.plugins.data_sources import DataQuery
from backend.security.resource_credentials import resource_secret_resolver
from backend.world.models import EdgeDirection


class ResourceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict = Field(default_factory=dict)
    confirm: bool = False


async def invoke_resource_action(services, node_id, action, request, *, capability=None, data_reader=None, data_relationship=None):
    # Hold this lock until the worker has stopped, including cancellation. An
    # edge revocation or deletion can never overtake a running resource write.
    async with services._node_mutation():
        node = services.world.get_card(node_id)
        operation = services.plugins.node_type(node.type).resource_actions.get(action)
        if data_reader is not None:
            from backend.data_sources import connected_sources
            if data_relationship is not None:
                # Before connection confirmation, expose schema metadata only.
                # No provisional edge and no data reads are authorized here.
                reader = services.world.get_card(data_reader)
                relation = services.plugins.relationship(data_relationship)
                if action != "schemas" or not relation.data_read or data_reader == node_id:
                    raise PermissionDeniedError("This relationship does not allow schema selection")
                services.world._assert_valid_relationship(reader.type, node.type, data_relationship)
                services.world._assert_valid_direction(reader.type, node.type, data_relationship, EdgeDirection.FORWARD)
            elif node_id not in {item["id"] for item in connected_sources(services, data_reader)}:
                raise PermissionDeniedError("Connect this data source before reading it")
            source = services.plugins.node_type(node.type).data_source
            if source is None or action not in {"schemas", "read"}:
                raise ResourceValidationError("Unknown data source operation")
            def read_dataset(context, arguments):
                result = source.schemas(context) if action == "schemas" else source.read(context, DataQuery.model_validate(arguments))
                if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) > 4 * 1024 * 1024:
                    raise ResourceValidationError("Dataset exceeds 4 MiB; select fewer fields or a smaller limit")
                return result
            operation = NodeResourceAction(read_dataset)
        if operation is None:
            raise ResourceValidationError("Unknown resource action")
        if capability is not None:
            live = services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.target_id != node_id or live.kind != operation.capability_kind:
                raise PermissionDeniedError("This connection does not allow that resource action")
            if request.confirm:
                raise PermissionDeniedError("Agents cannot supply desktop confirmation")
        context = NodeResourceContext(node_id, services.resources.node_storage_path(node_id), Event(),
            actor_id=capability.agent_id if capability else None, confirmed=request.confirm,
            state=services.card_state.bind(node_id),
            resolve_secret=resource_secret_resolver(services, node))
        task = asyncio.create_task(asyncio.to_thread(operation.handler, context, request.arguments))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            context.cancelled.set()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # Retrieve any worker error before releasing the lock.
            raise
        except ValueError as error:
            raise ResourceValidationError(validation_message(error)) from error
