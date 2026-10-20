"""OAW Run adapter for the isolated, Pack-owned MatCreator engine.

Only this process owns graph authorization. The worker receives no world DB,
Sandbox paths or HTTP credentials, and every reverse tool RPC is reauthorized.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import open_agent_world

from open_agent_world.plugin_api import (
    AgentConfigurationError, AgentDependencyError, AgentEvent, AgentEventType,
    AgentInfo, AgentNotFoundError, AgentStateError, AgentStatus, RuntimeProvider,
    codex_tool_content,
)
from open_agent_world.plugin_api.tool_execution import execute_tool
from .native_card import NativeConfig
from .native_environment import ENGINE, read_environment
from .native_projection import project_history


def _redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[redacted]") if secret else value
    if isinstance(value, dict):
        return {k: _redact(v, secret) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secret) for v in value]
    return value


class MatCreatorRuntime(RuntimeProvider):
    def __init__(self, capability_provider):
        self.provider = capability_provider
        self.records = {}
        self.active = {}

    def _settings(self, config):
        values = NativeConfig.model_validate({**dict(config.provider_config), "model": config.model,
            "system_instruction": config.system_instruction, "max_concurrent_runs": config.max_concurrent_runs})
        return values

    def _read_environment(self):
        root = getattr(self.provider, "managed_environment_root", None)
        return read_environment(data_root=root()) if callable(root) else read_environment()

    async def create_agent(self, config):
        self._settings(config)
        if config.agent_id in self.records:
            raise AgentStateError("MatCreator Agent already exists")
        record = AgentInfo(config, AgentStatus.IDLE, "matcreator-" + config.agent_id)
        self.records[config.agent_id] = record
        return record

    async def update_agent(self, config):
        await self.get_agent(config.agent_id)
        self._settings(config)
        if any(owner == config.agent_id for owner, _ in self.active.values()):
            raise AgentStateError("Cannot update a running MatCreator Agent")
        self.records[config.agent_id] = replace(self.records[config.agent_id], config=config)
        return self.records[config.agent_id]

    async def delete_agent(self, agent_id):
        await self.get_agent(agent_id)
        for run, (owner, _) in list(self.active.items()):
            if owner == agent_id:
                await self.stop(run)
        # Retain checkpoints with other Run history; never reuse them for new IDs.
        del self.records[agent_id]

    async def get_agent(self, agent_id):
        if agent_id not in self.records:
            raise AgentNotFoundError(agent_id)
        record = self.records[agent_id]
        settings = self._settings(record.config)
        try:
            executable, directory = self._read_environment()
            environment = {"status": "ready", "python": executable, "directory": str(directory)}
        except RuntimeError as exc:
            environment = {"status": "missing", "error": str(exc)}
        return replace(record, details={"engine": "Pack-owned MatCreator", "source": str(ENGINE),
            "mode": settings.agent_mode, "environment": environment})

    async def _bridge(self, agent_id, request):
        definitions = await self.provider.list_tools(agent_id)
        visible = {tool.capability_id: tool for tool in definitions}
        if request["method"] == "list_tools":
            return [{"capability_id": t.capability_id, "name": t.name, "description": t.description,
                "input_schema": t.input_schema} for t in visible.values()]
        if request["method"] != "invoke_tool":
            raise AgentStateError("Unknown MatCreator bridge operation")
        cap, arguments = request.get("capability_id"), request.get("arguments")
        if cap not in visible or not isinstance(arguments, dict):
            return {"ok": False, "error": {"code": "permission_denied", "message": "Tool unavailable or revoked; list tools again."}}
        async def invoke():
            value = await self.provider.invoke_tool(agent_id, cap, arguments)
            if visible[cap].name == 'send_conversation_message' and isinstance(value, dict) and value.get('id'):
                # The persisted message remains complete; the model needs only a receipt.
                return {"message_id": value['id'], "delivered": True,
                    "note": "Already visible to the user. Do not repeat or summarize this message in the final response."}
            return value

        result = await execute_tool(invoke,
            serialize=codex_tool_content)
        return {"tool": visible[cap].name, "result": result.response, "ok": result.ok}

    async def _snapshot(self, agent_id, run_id, value):
        await self.provider.publish_execution(agent_id, run_id, project_history(value))

    def _worker_environment(self):
        """Deployment adapters may narrow inherited process configuration."""
        env = {k: v for k, v in os.environ.items() if not k.startswith((
            "MATCREATOR_", "MATCLAW_", "LLM_", "KDG_", "BOHRIUM_", "OPENAI_", "ANTHROPIC_", "GEMINI_", "GOOGLE_API_"))}
        env.update(PYTHONUTF8="1", PYTHONUNBUFFERED="1")
        return env

    async def execute(self, config, context, runtime_input):
        settings = self._settings(config)
        await self.get_agent(config.agent_id)
        if any(owner == config.agent_id for owner, _ in self.active.values()):
            raise AgentStateError("MatCreator Agent already has an active Run")
        model = self.provider.resolve_model(config.model)
        if model.adapter == "typesafe":
            raise AgentConfigurationError("MatCreator uses ADK/LiteLLM. Select an OpenAI, Anthropic, Gemini or legacy model connection.")
        scope = hashlib.sha256((context.context_id or "direct").encode()).hexdigest()
        root = self.provider.runtime_state_directory(config.agent_id) / "matcreator" / scope
        root.mkdir(parents=True, exist_ok=True)
        worker = Path(__file__).with_name("native_worker.py")
        try:
            executable, managed = self._read_environment()
        except RuntimeError as exc:
            raise AgentDependencyError(str(exc)) from exc
        # Runtime-specific configuration is constructed inside the worker before
        # importing the engine. Do not inherit ambient model or MatCreator settings.
        env = self._worker_environment()
        process = None
        pending = set()
        write_lock = asyncio.Lock()
        stderr_task = None
        secret = model.api_key
        try:
            try:
                process = await asyncio.create_subprocess_exec(executable,
                    "-I", "-S", "-X", "utf8", "-u", str(worker),
                    "--environment", str(managed),
                    cwd=root, env=env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    limit=12 * 1024 * 1024,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            except OSError as exc:
                raise AgentDependencyError("Cannot start MatCreator Python. Repair the plugin dependency installation.") from exc
            self.active[context.run_id] = (config.agent_id, process)

            async def write(value):
                async with write_lock:
                    process.stdin.write((json.dumps(value, ensure_ascii=False) + "\n").encode())
                    await process.stdin.drain()

            async def drain_stderr():
                # Logs can contain SDK request data. Never publish raw stderr.
                while await process.stderr.read(65536):
                    pass

            stderr_task = asyncio.create_task(drain_stderr())
            await write({"type": "start", "root": str(root),
                "public_api_package": str(Path(open_agent_world.__file__).parent),
                "model": asdict(model), "mode": settings.agent_mode, "instruction": config.system_instruction,
                "prompt": runtime_input.prompt, "run_id": context.run_id,
                "background_job_notification": getattr(context, "extensions", {}).get("background_job_notification")})

            async def respond(message):
                try:
                    if message['method'] == 'pending_conversation_input':
                        result = await self.provider.pending_conversation_input(config.agent_id, context.run_id)
                    elif message['method'] == 'acknowledge_conversation_input':
                        await self.provider.acknowledge_conversation_input(
                            config.agent_id, context.run_id, message['message_ids'])
                        result = {'ok': True}
                    else:
                        result = await self._bridge(config.agent_id, message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    result = {"ok": False, "error": {"message": "OAW tool bridge failed; inspect current tool state before retrying."}}
                await write({"type": "response", "id": message["id"], "value": result})

            completed = False
            while line := await process.stdout.readline():
                message = json.loads(line)
                kind = message.get("type")
                if kind == "request":
                    if len(pending) >= 32:
                        raise AgentStateError("MatCreator exceeded the concurrent tool request limit")
                    task = asyncio.create_task(respond(message))
                    pending.add(task)
                    # Keep tasks until the next read to observe protocol failures.
                elif kind == "event":
                    yield AgentEvent(config.agent_id, context.run_id, AgentEventType(message["event"]),
                        _redact(message.get("payload", {}), secret))
                elif kind == "snapshot":
                    projection = await execute_tool(lambda: self._snapshot(config.agent_id, context.run_id, _redact(message["value"], secret)))
                    if not projection.ok:
                        yield AgentEvent(config.agent_id, context.run_id, AgentEventType.PROGRESS,
                            {"phase": "Execution view unavailable", "detail": projection.response})
                elif kind == "heartbeat":
                    yield AgentEvent(config.agent_id, context.run_id, AgentEventType.PROGRESS, {"phase": "MatCreator running"})
                elif kind == "error":
                    raise AgentDependencyError(_redact(str(message["message"]), secret))
                elif kind == "completed":
                    completed = True
                    yield AgentEvent(config.agent_id, context.run_id, AgentEventType.COMPLETED,
                        {"text": _redact(message.get("text", ""), secret),
                         "workflow_status": message.get("workflow_status", "idle")}, run_status="succeeded")
                    break
                else:
                    raise AgentStateError("Invalid MatCreator worker message")
                for task in tuple(pending):
                    if task.done():
                        pending.remove(task)
                        task.result()
            if not completed:
                raise AgentStateError("MatCreator worker exited before completing the Run. Check runtime dependencies and retained session state.")
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            if process is not None:
                await self._close(process)
            if stderr_task:
                await stderr_task
            self.active.pop(context.run_id, None)

    async def _close(self, process):
        if process.returncode is None:
            with suppress(BrokenPipeError, ConnectionResetError):
                process.stdin.write(b'{"type":"stop"}\n')
                await process.stdin.drain()
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def stop(self, run_id):
        active = self.active.get(run_id)
        if active:
            await self._close(active[1])
            self.active.pop(run_id, None)
