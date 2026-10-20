"""Private JSON-lines worker. Run by native_runtime, never as an HTTP server."""
from __future__ import annotations

import asyncio
from contextlib import aclosing, suppress
import json
import os
from pathlib import Path
import sys
import traceback
import uuid

# Keep third-party import-time prints off the protocol channel.
WIRE = sys.stdout
sys.stdout = sys.stderr

# -I -S removes host/user site-packages and PYTHONPATH from managed workers.
# Add only the adapter and the Pack's installed orchestration dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_environment import activate, activate_engine
from native_events import is_conversation_author, public_events

if len(sys.argv) == 3 and sys.argv[1] == "--environment":
    activate(Path(sys.argv[2]))


def send(value):
    WIRE.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
    WIRE.flush()


def configure(request):
    # Load only the public package, without adding host site-packages or the
    # backend source root to this isolated worker's import search path.
    import importlib.util
    package = Path(request['public_api_package'])
    spec = importlib.util.spec_from_file_location('open_agent_world', package / '__init__.py',
                                                submodule_search_locations=[str(package)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['open_agent_world'] = module
    spec.loader.exec_module(module)
    activate_engine()
    root = Path(request["root"])
    model = request["model"]
    model_id = model.get("model_id", model.get("model"))
    if model["adapter"] != "legacy" and not model_id.startswith(model["adapter"] + "/"):
        model_id = model["adapter"] + "/" + model_id
    os.environ.update({
        "MATCREATOR_HOME": str(root / "home"), "MATCLAW_WORKSPACE": str(root / "workspace"),
        "MATCLAW_SESSION_DIR": str(root / "workspace"),
        "LLM_MODEL": model_id,
        "LLM_API_KEY": model["api_key"] or "", "LLM_BASE_URL": model["base_url"],
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    })
    return root


def emit_event(event, child=False):
    for message in public_events(event, child=child):
        send(message)


async def run(request, reader, start_reader):
    root = configure(request)
    # ADK imports LiteLLM lazily. Load its native tokenizer extensions before
    # starting the Windows stdin thread for the same reason as NumPy below.
    import litellm  # noqa: F401
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService, Session
    from google.genai import types
    from native_adapter import install, read_agent_history

    pending = {}
    stopped = False
    owner = asyncio.current_task()

    async def rpc(method, **arguments):
        if stopped:
            raise asyncio.CancelledError()
        ident = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        pending[ident] = future
        send({"type": "request", "id": ident, "method": method, **arguments})
        try:
            return await future
        finally:
            pending.pop(ident, None)

    async def receive():
        nonlocal stopped
        while True:
            message = await reader()
            if message is None or message.get("type") == "stop":
                stopped = True
                owner.cancel()
                return
            if message.get("type") != "response" or message.get("id") not in pending:
                raise RuntimeError("Invalid host response")
            pending[message["id"]].set_result(message["value"])

    from native_context import NativeContexts
    app = install(rpc, emit_event, NativeContexts(root, request['model'], send))
    app.root_agent.planning_agent.instruction += "\n" + request["instruction"]
    class CheckpointSessions(InMemorySessionService):
        live = None

        async def get_session(self, **kwargs):
            result = await super().get_session(**kwargs)
            if result is not None:
                self.live = result
            return result

    sessions = CheckpointSessions()
    checkpoint = root / "session.json"
    previous = Session.model_validate_json(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else None
    state = previous.state if previous else {}
    state.update(agent_mode=request["mode"], execution_approved=False, return_to_planner=False,
        workdir=str(root / "workspace"), workspace_dir=str(root / "workspace"),
        output_dir=None, session_output_dir=None)
    state["_background_job_notification"] = request.get("background_job_notification")
    session = await sessions.create_session(app_name=app.name, user_id="oaw", session_id="native", state=state)
    # InMemorySessionService stores the authoritative object separately from its
    # returned copy. Populate the supported Session object's event history.
    stored = sessions.sessions[app.name]["oaw"][session.id]
    if previous:
        from oaw_matcreator_engine.session_recovery import repair_interrupted_tools
        stored.events = repair_interrupted_tools(previous.events)

    def persist():
        # The engine's BaseAgent updates ctx.session.state directly as well as
        # emitting ADK deltas. Preserve both, including recovery/session IDs.
        if sessions.live is not None:
            stored.state = dict(sessions.live.state)
        temp = checkpoint.with_suffix(".tmp")
        temp.write_text(stored.model_dump_json(), encoding="utf-8")
        temp.replace(checkpoint)

    last_snapshot = None

    def snapshot(phase="running"):
        nonlocal last_snapshot
        graph = stored.state.get("execution_graph") or {}
        if isinstance(graph, list):
            graph = graph[0] if graph else {}
        graph_path = root / "home" / ".adk" / "agent_graphs" / "native.json"
        history = read_agent_history(graph_path)
        if history is None:
            return  # Retain the last published graph while history is unavailable.
        from oaw_matcreator_engine.agents.graph_logger import AgentGraphLogger
        goal = str(stored.state.get("goal") or "")
        try:
            if AgentGraphLogger(session.id).log_snapshot_context(phase, goal):
                history = read_agent_history(graph_path)
        except (OSError, ValueError):
            marker = "history_revision_unavailable"
            if last_snapshot != marker:
                send({"type": "event", "event": "agent_progress", "payload": {
                    "message": "Execution graph update is temporarily unavailable; work continues.",
                    "history_snapshot_unavailable": True}})
            last_snapshot = marker
            return
        if history is None:
            return
        value = {"phase": phase, "goal": goal, "graph": graph,
                 "history": {**history, "history_id": root.name}}
        # Detail is optional; topology and identities are never silently cut.
        # Keep comfortably below the parent's JSON-lines transport budget.
        if len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")) > 8 * 1024 * 1024:
            for node in history["nodes"].values():
                node.pop("summary", None)
            for plan in history.get("plans", {}).values():
                for node in plan.get("nodes", {}).values():
                    for field in ("action", "result"):
                        node.pop(field, None)
            value["history"]["detail_omitted"] = True
            value["graph"] = {}
        if len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")) > 10 * 1024 * 1024:
            # Observation must never abort scheduled work. Keep the last valid
            # projection; full history is still durable in the Pack directory.
            marker = "history_transport_limit"
            if last_snapshot != marker:
                send({"type": "event", "event": "agent_progress", "payload": {
                    "message": "Execution history exceeds the live graph transport limit; complete history remains saved.",
                    "history_snapshot_unavailable": True,
                    "total_nodes": len(history["nodes"])}})
            last_snapshot = marker
            return
        serialized = json.dumps(value, sort_keys=True, default=str)
        if serialized != last_snapshot:
            send({"type": "snapshot", "value": value})
            last_snapshot = serialized

    async def heartbeat():
        while True:
            await asyncio.sleep(2)
            persist()
            snapshot()
            send({"type": "heartbeat"})

    # On Windows, importing a native extension (such as LiteLLM's tokenizers)
    # while another thread blocks on CRT stdin can deadlock extension loading.
    # Finish dependency/agent construction before starting the pipe reader.
    persist()
    start_reader()
    receiver = asyncio.create_task(receive())
    ticker = asyncio.create_task(heartbeat())
    final_text = ""
    try:
        async with Runner(app=app, session_service=sessions) as runner:
            async with aclosing(runner.run_async(user_id="oaw", session_id=session.id,
                new_message=types.Content(role="user", parts=[types.Part.from_text(text=request["prompt"])]))) as events:
                async for event in events:
                    if stopped:
                        raise asyncio.CancelledError()
                    emit_event(event)
                    persist()
                    snapshot()
                    if is_conversation_author(event) and event.is_final_response():
                        texts = [p.text for p in (getattr(event.content, "parts", None) or []) if p.text and not p.thought]
                        if texts:
                            final_text = "\n".join(texts)
        from oaw_matcreator_engine.agents.execution_graph_state import get_execution_graph
        graph = get_execution_graph(stored.state) or {}
        waiting = any(node.get("status") == "waiting" for node in graph.get("nodes", {}).values())
        snapshot("waiting" if waiting else "idle")
        send({"type": "completed", "text": "" if waiting else final_text,
            "workflow_status": "waiting" if waiting else "idle"})
    except asyncio.CancelledError:
        from oaw_matcreator_engine.agents.graph_logger import AgentGraphLogger
        AgentGraphLogger(session.id).mark_running_nodes_cancelled()
        snapshot("interrupted")
        raise
    except Exception:
        from oaw_matcreator_engine.agents.graph_logger import AgentGraphLogger
        AgentGraphLogger(session.id).mark_running_nodes_failed()
        snapshot("error")
        raise
    finally:
        persist()
        ticker.cancel()
        receiver.cancel()
        await asyncio.gather(ticker, receiver, return_exceptions=True)


async def main():
    # A dedicated daemon reader works on Windows pipes and never prevents exit
    # while asyncio.run waits for its executor to shut down.
    import threading
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def read_lines():
        for line in sys.stdin:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, json.loads(line))
            except (ValueError, RuntimeError):
                break
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, None)

    first_line = sys.stdin.readline()
    request = json.loads(first_line) if first_line else None
    if not request or request.get("type") != "start":
        raise RuntimeError("Expected a start request")
    await run(request, queue.get, lambda: threading.Thread(target=read_lines, daemon=True).start())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        # Errors may mention model credentials; the parent redacts them before
        # turning this into a Run failure. Never send a Python traceback.
        send({"type": "error", "message": str(exc)})
        sys.exit(1)
