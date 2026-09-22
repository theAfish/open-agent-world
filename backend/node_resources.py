"""Dispatch native resource operations through live graph authorization."""
import asyncio
from threading import Event

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import PermissionDeniedError, ResourceValidationError
from backend.node_documents import validation_message
from backend.plugins.resources import NodeResourceContext


class ResourceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict = Field(default_factory=dict)
    confirm: bool = False


async def invoke_resource_action(services, node_id, action, request, *, capability=None):
    # Hold this lock until the worker has stopped, including cancellation. An
    # edge revocation or deletion can never overtake a running resource write.
    async with services._node_mutation():
        node = services.world.get_card(node_id)
        operation = services.plugins.node_type(node.type).resource_actions.get(action)
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
            state=services.card_state.bind(node_id))
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
