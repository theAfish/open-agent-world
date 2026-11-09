"""Task directories share runtime infrastructure while keeping paths consistent."""
from contextlib import contextmanager
from pathlib import Path

import pytest

from backend.capabilities.provider import WorldAgentCapabilityProvider, _CapabilityContext
from backend.errors import ResourceValidationError
from backend.runs import InvocationContext, InvocationCaller, RunStatus
from backend.runs.manager import _current_invocation
from backend.sandbox.task_workspace import initial_policy, binding
from backend.sandbox.files import working_path, command_path
from backend.sandbox.models import SandboxSecurityError, SandboxValidationError
from backend.tests.conftest import create_node
from backend.tests.test_skill_runtime import runtime_client, setup_skill


def new_run(services, agent, *, source=None, legacy=False, caller="user", task_id=None, parent=None, session=None):
    store = services.run_manager.store
    run = store.create(agent_id=agent["id"], runtime_provider_id="core.mock", caller_kind=caller,
                       task_id=task_id, parent_run_id=parent, context_id=session.id if session else None, caller_id=session.conversation_id if session else None)
    lifecycle = {"continuation_run_id": source} if source else {}
    if not legacy:
        lifecycle["task_workspace"] = initial_policy(store, agent_id=run.agent_id, run_id=run.run_id, caller_kind=run.caller_kind, task_id=run.task_id, lifecycle=lifecycle, context_id=run.context_id)
    return store.update_lifecycle(run.run_id, **lifecycle)


@contextmanager
def in_run(run):
    token = _current_invocation.set(InvocationContext(run_id=run.run_id, agent_id=run.agent_id,
        parent_run_id=run.parent_run_id, root_run_id=run.root_run_id, task_id=run.task_id,
        caller=InvocationCaller(run.caller_kind), context_id=run.context_id, runtime_provider_id=run.runtime_provider_id))
    try:
        yield
    finally:
        _current_invocation.reset(token)


def test_sessions_separate_same_filename_and_share_tool_command_skill_cwd(runtime_client):
    client, backend, native = runtime_client
    agent, sandbox, skill, *_ = setup_skill(client)
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    from backend.conversations import ConversationSessionCreate
    room = create_node(client, 'conversation')
    sessions = [services.conversations.create_session(room['id'], ConversationSessionCreate(title=name)) for name in ('A', 'B')]
    first, second = [new_run(services, agent, session=session) for session in sessions]
    async def scenario(run, content):
        with in_run(run):
            async def call(name, **args):
                return await provider.invoke_tool(agent["id"], "operation:" + name, {"sandbox": sandbox["id"], **args})
            info = await call("inspect_sandbox")
            assert info["task_workspace"]["directory"] == next(session.title for session in sessions if session.id == run.context_id)
            await call("write_sandbox_text_file", path="scripts/build.py", content=content)
            page = await call("read_sandbox_file", path="scripts/build.py")
            assert page["text"] == content
            entries = await call("list_sandbox_files", query="build.py")
            assert [item["path"] for item in entries["entries"]] == ["scripts/build.py"]
            result = await call("execute_command", argv=["cmd.exe", "/c", "echo task"], wait_seconds=None)
            assert result["working_directory"] == next(session.title for session in sessions if session.id == run.context_id)
            root = backend._sandboxes_root / sandbox["id"] / "workspace"
            assert native.last_cwd == root / next(session.title for session in sessions if session.id == run.context_id)
            await call("run_skill_script", skill=skill["id"], script="scripts/check.py", argv=[], wait_seconds=None)
            assert native.last_cwd == root / next(session.title for session in sessions if session.id == run.context_id)
            # Trusted plugin staging/import helpers use the same relative base.
            capability = services.capabilities.capability_for_id(agent["id"], "sandbox.execute:" + sandbox["id"])
            context = _CapabilityContext(services)
            await context.write_sandbox_workspace_file(capability, sandbox["id"], "inputs/native.json", b'{"ok":true}')
            assert await context.read_sandbox_workspace_file(capability, sandbox["id"], "inputs/native.json") == b'{"ok":true}'
    client.portal.call(scenario, first, "print('first')")
    client.portal.call(scenario, second, "print('second')")
    root = backend._sandboxes_root / sandbox["id"] / "workspace"
    assert (root / sessions[0].title / "scripts/build.py").read_text() == "print('first')"
    assert (root / sessions[1].title / "scripts/build.py").read_text() == "print('second')"
    assert not (root / "scripts").exists()
    assert len(client.get(f"/api/sandboxes/{sandbox['id']}/task-workspaces").json()["items"]) == 2


def test_workspace_labels_follow_session_titles_without_using_prompts_or_moving_files(runtime_client):
    from backend.conversations import ConversationSessionCreate
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    room = create_node(client, "conversation")
    session = services.conversations.create_session(room['id'], ConversationSessionCreate(title="test"))
    store = services.run_manager.store
    run = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='conversation',
        caller_id=room['id'], context_id=session.id)
    run = store.update_lifecycle(run.run_id, task_workspace=initial_policy(store, agent_id=agent['id'],
        run_id=run.run_id, caller_kind='conversation'))
    scope = services.state.ensure_scope('run', run.run_id)
    services.state.set(scope, 'task_prompt', 'A full user prompt must not name this directory', run_id=run.run_id)
    provider = WorldAgentCapabilityProvider(services)
    async def write():
        with in_run(run):
            await provider.invoke_tool(agent['id'], 'operation:write_sandbox_text_file',
                {'sandbox': sandbox['id'], 'path': 'saved.txt', 'content': 'preserve bytes'})
    client.portal.call(write)
    url = f"/api/sandboxes/{sandbox['id']}/task-workspaces"
    first = client.get(url).json()['items'][0]
    assert first['label'] == 'test'
    services.conversations.rename_session(room['id'], session.id, 'Renamed experiment')
    renamed = client.get(url).json()['items'][0]
    assert renamed['label'] == 'Renamed experiment' and renamed['directory'] == first['directory']
    saved = backend._sandboxes_root / sandbox['id'] / 'workspace' / first['directory'] / 'saved.txt'
    assert saved.read_text() == 'preserve bytes'


def test_task_relative_missing_paths_are_read_errors_and_absent_shared_is_empty(runtime_client):
    from backend.errors import ResourceValidationError
    client, backend, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    run = new_run(services, agent)
    provider = WorldAgentCapabilityProvider(services)
    async def scenario():
        with in_run(run):
            with pytest.raises(ResourceValidationError, match='assigned workspace directory'):
                await provider.invoke_tool(agent['id'], 'operation:list_sandbox_files',
                    {'sandbox': sandbox['id'], 'path': 'tasks/' + run.run_id})
            shared = await provider.invoke_tool(agent['id'], 'operation:list_sandbox_files',
                {'sandbox': sandbox['id'], 'scope': 'shared'})
            assert shared['entries'] == [] and shared['exists'] is False
    client.portal.call(scenario)
    assert not (backend._sandboxes_root / sandbox['id'] / 'workspace/shared').exists()


def test_session_sharing_continuation_and_legacy_bindings(runtime_client):
    client, _, _ = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    services = client.app.state.services
    first = new_run(services, agent, caller="work", task_id="board:item")
    async def directory(run):
        with in_run(run):
            return binding(services, sandbox["id"], agent["id"])["directory"]
    original = client.portal.call(directory, first)
    retry = new_run(services, agent, caller="work", task_id="board:item")
    continued = new_run(services, agent, source=first.run_id)
    child = new_run(services, agent, parent=first.run_id)
    independent = new_run(services, agent, task_id="board:item")
    assert client.portal.call(directory, retry) == client.portal.call(directory, continued) == original
    assert client.portal.call(directory, child) == original
    assert client.portal.call(directory, independent) == original
    legacy = new_run(services, agent, legacy=True)
    old_continued = new_run(services, agent, source=legacy.run_id)
    assert client.portal.call(directory, old_continued) == ""
    # Persisted bindings survive loss of process-local state/reconstruction.
    assert services.run_manager.store.get(continued.run_id).lifecycle["sandbox_workspaces"][sandbox["id"]]["directory"] == original


def test_run_admission_uses_workspace_policy_without_a_task_board(runtime_client):
    client, _, _ = runtime_client
    agent = create_node(client, "agent")
    services = client.app.state.services
    async def scenario():
        first = await services.run_manager.start_run(agent["id"], "one")
        await services.run_manager.wait_settled(first.run_id)
        second = await services.run_manager.start_run(agent["id"], "two")
        await services.run_manager.wait_settled(second.run_id)
        assert first.task_id is second.task_id is None
        for run in (first, second):
            saved = services.run_manager.store.get(run.run_id)
            assert saved.lifecycle["task_workspace"] == {"mode": "workspace", "origin_run_id": run.run_id}
    client.portal.call(scenario)


def test_cwd_validation_preserves_native_path_boundary_and_manual_api(runtime_client, tmp_path):
    from backend.sandbox.linux import bubblewrap_command
    from backend.sandbox.models import ResourceAccess
    client, backend, native = runtime_client
    agent, sandbox, *_ = setup_skill(client)
    root = backend._sandboxes_root / sandbox["id"] / "workspace"
    (root / "tasks/example").mkdir(parents=True)
    reply = client.post(f"/api/sandboxes/{sandbox['id']}/execute", json={"argv": ["cmd.exe", "/c", "echo task"], "working_directory": "tasks/example"})
    assert reply.status_code == 200, reply.text
    assert native.last_cwd == root / "tasks/example"
    for invalid in ("../outside", "/tmp", "a\\b", "a:b"):
        with pytest.raises((ValueError, SandboxValidationError)):
            command_path(invalid)
    (root / "linked").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises((OSError, SandboxSecurityError)):
        working_path(root, "linked")
    assert command_path("tasks/example") == "/workspace/tasks/example"
    argv = bubblewrap_command(root, ResourceAccess.READ_WRITE, (), ["echo", "task"], {}, working_directory="tasks/example")
    assert argv[argv.index("--chdir") + 1] == "/workspace/tasks/example"
    assert str(root) in argv and "/workspace" in argv


def test_packaged_wsl_worker_resolves_task_cwd_without_host_run_modules(tmp_path):
    import json
    import subprocess
    import sys
    from backend.sandbox.wsl import _BOOTSTRAP, _envelope
    (tmp_path / "tasks/example").mkdir(parents=True)
    payload = json.loads(_envelope({"workspace": str(tmp_path), "directory": "tasks/example"}))
    # Load the complete shipped worker, then probe its real cwd/file helpers
    # without requiring a WSL kernel or launching an unisolated user command.
    probe = '''import json
from pathlib import Path
from . import linux_worker
from .files import working_path
from .linux import bubblewrap_command
from .models import ResourceAccess
def main(request, **options):
 root=Path(request['workspace'])
 actual=working_path(root, request['directory'])
 argv=bubblewrap_command(root, ResourceAccess.READ_WRITE, (), ['echo','task'], {}, working_directory=request['directory'])
 print(json.dumps({'cwd':argv[argv.index('--chdir')+1], 'host_directory':str(actual)}))
linux_worker.main=main
'''
    payload["modules"].append(["task_cwd_probe", probe])
    result = subprocess.run([sys.executable, "-I", "-c", _BOOTSTRAP],
        input=json.dumps(payload).encode() + b"\n", capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"cwd": "/workspace/tasks/example", "host_directory": str(tmp_path / "tasks/example")}


def test_artifact_versions_keep_portable_paths_and_materialize_into_the_consuming_task(runtime_client):
    from backend.tests.test_artifacts import setup
    client, _, agent, sandbox, collection, root, _ = setup(runtime_client)
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    producer, consumer = new_run(services, agent), new_run(services, agent)
    async def scenario():
        with in_run(producer):
            await provider.invoke_tool(agent["id"], "operation:write_sandbox_text_file",
                {"sandbox": sandbox["id"], "path": "outputs/result.txt", "content": "produced"})
            with pytest.raises(ResourceValidationError, match="not its root"):
                await provider.invoke_tool(agent["id"], "operation:publish_artifact",
                    {"collection": collection["id"], "sandbox": sandbox["id"], "paths": [""],
                     "request_key": "bad-root", "finalized": True, "name": "result"})
            assert services.resources.artifacts.all() == []
            version = await provider.invoke_tool(agent["id"], "operation:publish_artifact",
                {"collection": collection["id"], "sandbox": sandbox["id"], "paths": ["outputs/result.txt"],
                 "request_key": "task-result", "finalized": True, "name": "result"})
            assert version["manifest"][0]["path"] == "outputs/result.txt"
            assert version["provenance"]["workspace_directory"] == "Default session"
        with in_run(consumer):
            result = await provider.invoke_tool(agent["id"], "operation:materialize_artifact",
                {"collection": collection["id"], "sandbox": sandbox["id"], "version_id": version["version_id"], "destination": "inputs"})
            assert result["destination"] == "inputs"
            assert (root / "Default session" / "inputs/outputs/result.txt").read_text() == "produced"
    client.portal.call(scenario)


def test_report_collects_only_the_selected_task_and_explicit_continuation_chain(runtime_client, tmp_path):
    import json
    from backend.tests.test_agent_run_history_tools import saved_run, connect
    client, backend, _ = runtime_client
    source, sandbox, *_ = setup_skill(client)
    reviewer = create_node(client, "atomsculptor.log-review-agent")
    connect(client, reviewer, source, "agent.run_history")
    connect(client, reviewer, sandbox, "execute")
    services = client.app.state.services
    provider = WorldAgentCapabilityProvider(services)
    foreign = new_run(services, source, legacy=True)
    first = saved_run(services, source, sandbox)
    first = services.run_manager.store.update_lifecycle(first.run_id, task_workspace=initial_policy(services.run_manager.store, agent_id=first.agent_id, run_id=first.run_id, caller_kind=first.caller_kind))
    services.run_manager.store.update_status(first.run_id, RunStatus.FAILED)
    latest = saved_run(services, source, sandbox)
    latest = services.run_manager.store.update_lifecycle(latest.run_id, continuation_run_id=first.run_id,
        task_workspace=initial_policy(services.run_manager.store, agent_id=latest.agent_id, run_id=latest.run_id, caller_kind=latest.caller_kind, lifecycle={"continuation_run_id": first.run_id}))
    review_run = new_run(services, reviewer)
    root = backend._sandboxes_root / sandbox["id"] / "workspace"
    (root / "shared").mkdir()
    (root / "shared/explicit.txt").write_text("shared input")
    (root / "shared/unrelated.txt").write_text("not selected")
    async def scenario():
        for run, path in ((foreign, "other-task.txt"), (first, "scripts/first.py"), (latest, "outputs/final.extxyz")):
            with in_run(run):
                await provider.invoke_tool(source["id"], "operation:write_sandbox_text_file",
                    {"sandbox": sandbox["id"], "path": path, "content":
                     '1\nProperties=species:S:1:pos:R:3\nCu 0 0 0\n' if path.endswith('.extxyz') else "saved data"})
        with in_run(latest):
            read = await provider.invoke_tool(source["id"], "operation:read_sandbox_file",
                {"sandbox": sandbox["id"], "scope": "shared", "path": "explicit.txt"})
            assert read["text"] == "shared input"
        with in_run(review_run):
            return await provider.invoke_tool(reviewer["id"], "operation:export_agent_run",
                {"agent": source["id"], "sandbox": sandbox["id"], "run_id": latest.run_id})
    receipt = client.portal.call(scenario)
    root = backend._sandboxes_root / sandbox["id"] / "workspace"
    archive = root / "Default session" / receipt["archive_path"]
    assert receipt["run_ids"] == [first.run_id, latest.run_id]
    assert receipt["suggested_output_folder"].startswith("reports/")
    collection = json.loads((archive / "collection.json").read_text())
    assert collection["source_directories"][sandbox["id"]] == ["Default session"]
    assert receipt["collected_files"] == 3
    assert not list(archive.rglob("other-task.txt"))
    assert not list(archive.rglob("unrelated.txt"))
    assert list(archive.rglob("explicit.txt"))
    assert list(archive.rglob("final.extxyz")) and list(archive.rglob("first.py"))
    # Cross the real exporter/packaged CLI boundary using the same task-relative
    # paths a model receives. No source modelling program is executed.
    import subprocess
    import sys
    from oaw_atomsculptor.review_agent import log_review_skills
    scripts = tmp_path / "review-bundle"
    for name, content in log_review_skills().skills[0].files.items():
        path = scripts / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    task_root = root / "Default session"
    cli = scripts / "scripts/log_review.py"
    inspection = subprocess.run([sys.executable, str(cli), "inspect_archive", "--folder", receipt["archive_path"]],
        cwd=task_root, capture_output=True, text=True, timeout=30)
    assert inspection.returncode == 0, inspection.stderr
    inspected = json.loads(inspection.stdout)
    draft_path = task_root / inspected["manifest_file"]
    draft = json.loads(draft_path.read_text())
    draft["nodes"][-1]["structures"] = [{"path": str(next(archive.rglob("final.extxyz")).relative_to(archive)),
        "provenance": "collection_snapshot", "note": "Current saved-file preview, not an earlier version."}]
    draft_path.write_text(json.dumps(draft))
    built = subprocess.run([sys.executable, str(cli), "build_review", "--folder", receipt["archive_path"],
        "--manifest-file", inspected["manifest_file"], "--output-folder", receipt["suggested_output_folder"]],
        cwd=task_root, capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stderr
    result = json.loads(built.stdout)
    assert result["statistics"]["attempts"] == 2 and result["previews"] == 1, result.get('warnings')
    assert (task_root / result["report"]).is_file()
    assert (task_root / result["bundle"]).is_file()
