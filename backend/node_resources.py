"""Dispatch native resource operations through live graph authorization."""
import asyncio
import logging
import threading
from threading import Event

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import NotFoundError, PermissionDeniedError, ResourceValidationError
from backend.node_documents import validation_message
from backend.plugins.resources import NodeMember, NodeResourceContext

logger = logging.getLogger(__name__)


class ResourceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict = Field(default_factory=dict)
    confirm: bool = False


def _context(services, node_id, *, actor_id=None, confirmed=False, cancelled=None):
    return NodeResourceContext(node_id, services.resources.node_storage_path(node_id), cancelled or Event(),
        actor_id=actor_id, confirmed=confirmed, state=services.card_state.bind(node_id),
        background=services.resource_jobs.starter(node_id) if services.resource_jobs else None,
        members=_members(services, node_id))


def _members(services, node_id):
    node = services.world.get_card(node_id)
    if services.plugins.node_type(node.type).container is None:
        return ()
    return tuple(NodeMember(member.id, member.type, member.name, services.resources.node_storage_path(member.id),
                            dict(member.config)) for member in services.world.list_members(node_id))


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
        context = _context(services, node_id, actor_id=capability.agent_id if capability else None,
                           confirmed=request.confirm)
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


class ResourceJobs:
    """Slow work started by resource actions, so it never holds the graph barrier.

    ``work`` runs in a daemon thread without any host lock (shutdown does not wait
    for it); ``commit`` then runs under the node mutation lock like any resource
    action, and is skipped if the node was deleted or the host is stopping.
    When the commit is skipped or fails while the host keeps running, the
    optional ``abandon`` callback runs so the plugin stops reporting the job.
    Jobs do not survive a restart; plugins record enough to show that.
    """

    def __init__(self, services) -> None:
        self.services = services
        self.cancelled = Event()
        self.tasks: set[asyncio.Task] = set()

    def starter(self, node_id):
        # Resource handlers run in worker threads; capture the loop and type now, on the loop.
        loop = asyncio.get_running_loop()
        node_type = self.services.world.get_card(node_id).type

        def start(work, commit, abandon=None):
            loop.call_soon_threadsafe(self._spawn, node_id, node_type, work, commit, abandon)
        return start

    def _spawn(self, node_id, node_type, work, commit, abandon):
        if self.cancelled.is_set():
            return
        task = asyncio.create_task(self._run(node_id, node_type, work, commit, abandon),
                                   name=f"resource-job:{node_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _run(self, node_id, node_type, work, commit, abandon):
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def target():
            try:
                outcome = work(self.cancelled)
            except Exception as error:  # Handed to commit, which records it.
                outcome = error
            try:
                loop.call_soon_threadsafe(lambda: future.done() or future.set_result(outcome))
            except RuntimeError:
                pass  # The loop closed during shutdown.

        threading.Thread(target=target, name=f"resource-job:{node_id}", daemon=True).start()
        outcome = await future
        if self.cancelled.is_set():
            return
        committed = False
        try:
            async with self.services._node_mutation():
                try:
                    if self.services.world.get_card(node_id).type != node_type:
                        return
                except NotFoundError:
                    return
                await asyncio.to_thread(commit, _context(self.services, node_id), outcome)
                committed = True
        except Exception:
            # e.g. a pending deletion blocks writes until the next startup.
            logger.exception("Background resource job for node %s could not commit", node_id)
        finally:
            if not committed and abandon is not None and not self.cancelled.is_set():
                try:
                    abandon()
                except Exception:
                    logger.exception("Background resource job for node %s could not abandon", node_id)

    async def wait(self) -> None:
        """Wait for current jobs (tests and orderly shutdown of fast work)."""
        while self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        self.cancelled.set()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)
