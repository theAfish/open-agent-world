"""Host-owned EventHub consumer; durable Runs remain authoritative."""
import asyncio
import logging

from backend.errors import NotFoundError, PermissionDeniedError
from backend.execution_graph import connected_sources
from backend.plugins.run_observation import ObservedRun

logger = logging.getLogger(__name__)


def error_summary(error):
    """Return only fixed diagnostic categories; raw errors can contain secrets."""
    if not error:
        return None
    message = str(error).lower()
    categories = (
        (("invalid model", "model_not_found", "model not found"), "模型名称无效，或当前模型服务不提供该模型。"),
        (("authenticationerror", "invalid api key", "incorrect api key", "unauthorized", "401"), "模型服务认证失败，请检查密钥配置。"),
        (("permissiondenied", "permission denied", "forbidden", "403"), "服务拒绝访问，请检查账号或资源权限。"),
        (("insufficient_quota", "quota exceeded", "insufficient balance"), "服务额度或余额不足。"),
        (("ratelimit", "rate limit", "429"), "服务请求频率或额度达到限制，请稍后重试。"),
        (("timeout", "timed out"), "服务请求超时，未在规定时间内响应。"),
        (("connectionerror", "connection error", "connection refused", "connection reset", "connecterror"), "无法连接服务，或连接被中断。"),
        (("context_length_exceeded", "maximum context length", "context window"), "输入内容超过模型上下文容量。"),
        (("badrequesterror", "bad request", "400"), "服务拒绝了请求，请检查模型配置和请求参数。"),
    )
    return next((summary for matches, summary in categories if any(token in message for token in matches)),
                "运行发生错误，请在 Agent 运行详情中查看具体原因。")


class _Subscription:
    def __init__(self, task):
        self.task = task

    async def close(self):
        if self.task is asyncio.current_task():
            self.task.cancel()
            return
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)


class RunObservationService:
    def __init__(self, services):
        self.services = services
        self.tasks = set()
        self.closed = False
        self.reconcile_seconds = 30

    def _read(self, viewer_id, agent_id, run_id):
        services = self.services
        agent = services.world.get_card(agent_id)
        if agent_id not in {source['id'] for source in connected_sources(services, viewer_id)}:
            raise PermissionDeniedError('Connect this Agent as an execution source before observing it')
        if not services.plugins.has_trait(agent.type, 'core.agent'):
            raise PermissionDeniedError('Run observation requires an Agent source')
        run = services.run_manager.get_run(run_id)
        if run.agent_id != agent_id:
            raise PermissionDeniedError('This Run belongs to another Agent')
        return ObservedRun(run.run_id, run.agent_id, run.status.value,
            run.updated_at.isoformat(),
            run.started_at.isoformat() if run.started_at else None,
            run.finished_at.isoformat() if run.finished_at else None,
            error=error_summary(run.error) if run.status.value == "failed" else None)

    async def read(self, viewer_id, agent_id, run_id):
        async with self.services._node_mutation(read_only=True):
            return self._read(viewer_id, agent_id, run_id)

    async def watch(self, viewer_id, agent_id, run_id, callback):
        if self.closed:
            raise RuntimeError('Run observation service is shutting down')
        # Subscribe before the initial read so rapid completion cannot fall in a gap.
        subscription = self.services.events.subscribe()
        queue = await subscription.__aenter__()
        try:
            async with self.services._node_mutation(read_only=True):
                initial = self._read(viewer_id, agent_id, run_id)
                await callback(initial)
        except BaseException:
            await subscription.__aexit__(None, None, None)
            raise

        if self.closed:
            await subscription.__aexit__(None, None, None)
            raise RuntimeError("Run observation service is shutting down")
        ready = asyncio.Event()

        async def consume():
            previous = initial
            try:
                ready.set()
                while True:
                    # Recheck on all events, including graph changes, and on timeout.
                    # This also recovers when EventHub drops an older queue entry.
                    try:
                        await asyncio.wait_for(queue.get(), self.reconcile_seconds)
                    except TimeoutError:
                        pass
                    async with self.services._node_mutation(read_only=True):
                        try:
                            current = self._read(viewer_id, agent_id, run_id)
                        except PermissionDeniedError:
                            # Retain the request, but deliver nothing until a live
                            # grant returns. Graph events/timeouts reconcile it.
                            continue
                        if current != previous:
                            try:
                                await callback(current)
                            except Exception:
                                logger.exception("Backend Run observation callback failed; will retry")
                            else:
                                previous = current
            except asyncio.CancelledError:
                raise
            except NotFoundError:
                pass  # A deleted node or Run cannot be observed again.
            except Exception:
                logger.exception('Backend Run observation stopped')
            finally:
                await subscription.__aexit__(None, None, None)

        task = asyncio.create_task(consume())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        handle = _Subscription(task)
        try:
            await ready.wait()
        except BaseException:
            await handle.close()
            raise
        return handle

    async def shutdown(self):
        self.closed = True
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
