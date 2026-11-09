"""Scoped Run discovery/export over existing native records and Sandbox transfers."""
import asyncio
import base64
from datetime import UTC, datetime
import json
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import DomainError, NotFoundError, PermissionDeniedError, ResourceValidationError
from backend.plugins.registry import CapabilityDefinition, CapabilitySelector
from backend.runs import TERMINAL_RUN_STATUSES
from backend.sandbox.files import parts
from backend.sandbox.transfers import CHUNK_BYTES, MAX_ENTRIES
from open_agent_world.run_export import export_run

LIST = "agent.runs.list"
EXPORT = "agent.runs.export"


class RunList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class RunExport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sandbox_id: str
    run_id: str | None = Field(default=None, description="Omit for this Agent's latest root Run; an active latest Run is reported, never replaced by an older one.")
    include_task_attempts: bool = Field(default=True, description="Include settled root Runs linked by this Agent's native task_id or explicit continuation_run_id.")
    include_sandbox_files: bool = Field(default=True, description="Capture current workspaces referenced by native records, only with independent Sandbox execute grants. These are collection snapshots, not historical versions.")


def authorize(services, capability, action):
    live = services.capabilities.capability_for_id(capability.agent_id, capability.id)
    if live.kind != {"list": LIST, "export": EXPORT}[action] or live.target_id != capability.target_id:
        raise PermissionDeniedError("Run-history access was revoked")
    return live


def root_runs(services, agent_id):
    return [row for row in services._require_run_manager().list_runs(agent_id=agent_id) if row.parent_run_id is None]


def source_sandboxes(services, archive):
    """Use native identities only; aliases and arbitrary host paths do not grant access."""
    found = set()
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"sandbox", "sandbox_id"} and isinstance(child, str):
                    card = services.world.maybe_get_card(child)
                    if card and "core.sandbox" in services.plugins.node_type(card.type).traits:
                        found.add(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for path in archive.glob("attempts/*/run.json"):
        saved = json.loads(path.read_text())
        for sandbox_id in saved.get("lifecycle", {}).get("sandbox_workspaces", {}):
            card = services.world.maybe_get_card(sandbox_id)
            if card and "core.sandbox" in services.plugins.node_type(card.type).traits:
                found.add(sandbox_id)
    for path in archive.glob("attempts/*/*.json"):
        if path.name in {"sandbox logs.json", "tool calls.json", "tool receipts.json", "execution checkpoint.json"}:
            visit(json.loads(path.read_text()))
    return sorted(found)


async def invoke(services, capability, action, arguments):
    from backend.capabilities.provider import _validate_tool_request
    authorize(services, capability, action)
    if action == "list":
        request = _validate_tool_request(RunList, arguments)
        rows = list(reversed(root_runs(services, capability.target_id)))
        keys = {"run_id", "task_id", "status", "created_at", "started_at", "finished_at", "runtime_provider_id"}
        return {"agent_id": capability.target_id, "items": [row.model_dump(mode="json", include=keys)
            for row in rows[request.offset:request.offset + request.limit]], "total": len(rows),
            "next_offset": request.offset + request.limit if request.offset + request.limit < len(rows) else None}
    if action != "export":
        raise ResourceValidationError("Unknown Run-history action")
    request = _validate_tool_request(RunExport, arguments)
    rows = root_runs(services, capability.target_id)
    if not rows:
        raise NotFoundError("This connected Agent has no saved root Runs")
    selected = next((row for row in rows if row.run_id == request.run_id), None) if request.run_id else rows[-1]
    if selected is None:
        raise PermissionDeniedError("Run does not belong to the connected Agent's root history")
    included = {selected.run_id}
    if request.include_task_attempts:
        if selected.task_id:
            included.update(row.run_id for row in rows if row.task_id == selected.task_id)
        changed = True
        while changed:
            changed = False
            for row in rows:
                source = row.lifecycle.get("continuation_run_id")
                if source and (row.run_id in included or source in included):
                    additions = {row.run_id, source} & {r.run_id for r in rows}
                    if not additions <= included:
                        included.update(additions)
                        changed = True
    selected_rows = [row for row in rows if row.run_id in included]
    active = [row for row in selected_rows if row.status not in TERMINAL_RUN_STATUSES]
    if active:
        raise ResourceValidationError("Run history is still active: " + ", ".join(row.run_id + " (" + row.status + ")" for row in active)
            + ". Wait for completion, or select a settled Run and set include_task_attempts=false.")
    backend = services._require_sandbox_backend()

    async def file_op(sandbox_id, operation, **options):
        async with services._node_mutation(read_only=operation in {"list", "capture_manifest", "read_chunk"}):
            authorize(services, capability, action)
            services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
            if operation not in {"list", "capture_manifest", "read_chunk"}:
                from backend.workspace_context import assert_workspace_write
                assert_workspace_write(services, sandbox_id, options["path"])
            return await backend.file_operation(sandbox_id, operation, root="workspace", **options)

    warnings, files = [], []
    collected_at = datetime.now(UTC).isoformat()
    from backend.sandbox.task_workspace import ensure_directory, path_for, saved_binding
    async with services._node_mutation():
        authorize(services, capability, action)
        services.capabilities.require_sandbox_execute(capability.agent_id, request.sandbox_id)
        destination_workspace = await ensure_directory(services, request.sandbox_id, capability.agent_id)
    export_name = "oaw-run-review-" + uuid4().hex
    archive_relative = "inputs/" + export_name if destination_workspace["directory"] else export_name
    report_relative = "reports/" + export_name if destination_workspace["directory"] else export_name + "-report"
    prefix = path_for(destination_workspace["directory"], archive_relative)
    source_directories, source_shared_inputs = {}, {}
    with TemporaryDirectory(prefix="oaw-run-export-") as temporary:
        scratch = Path(temporary)
        archive = scratch / "archive"
        (archive / "attempts").mkdir(parents=True)
        attempts = []
        # Reuse the CLI's exact serializer and read-only DB snapshot. Never pass
        # database paths, provider configuration or bodies through model inputs.
        for number, row in enumerate(selected_rows, 1):
            async with services._node_mutation(read_only=True):
                authorize(services, capability, action)
                services.capabilities.require_sandbox_execute(capability.agent_id, request.sandbox_id)
                folder = await asyncio.to_thread(export_run, services.database.path, scratch / "exports",
                    run_id=row.run_id, label="runs", agent_id=capability.target_id, runtime_provider_id=None,
                    include_legacy_views=False)
            relative = f"attempts/{number:02d}-{row.run_id}"
            folder.rename(archive / relative)
            attempts.append({"directory": relative, "run_id": row.run_id})
            manifest_path = archive / relative / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["source_database"] = "OAW host-managed Run store"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        sandboxes = source_sandboxes(services, archive)
        if request.include_sandbox_files:
            if not sandboxes:
                warnings.append({"kind": "source_sandbox_identity_unavailable", "note": "No source Sandbox identity was retained in these Run records; no current files were inferred from names or host paths."})
            size = 0
            for sandbox_id in sandboxes:
                try:
                    services.capabilities.require_sandbox_execute(capability.agent_id, sandbox_id)
                except PermissionDeniedError:
                    warnings.append({"kind": "sandbox_access_missing", "sandbox_id": sandbox_id,
                        "note": "Connect this Sandbox with Execute to collect files; Run logs were retained."})
                    continue
                scopes = set()
                for row in selected_rows:
                    saved_scope = saved_binding(services, row, sandbox_id)
                    if saved_scope is not None:
                        scopes.add(saved_scope["directory"])
                    elif (row.lifecycle.get("task_workspace") or {}).get("mode", "legacy") == "legacy":
                        scopes.add("")
                if not scopes:
                    warnings.append({"kind": "task_directory_binding_unavailable", "sandbox_id": sandbox_id,
                        "note": "No native task-directory binding exists for this Sandbox; unrelated task files were not inferred."})
                    continue
                if "" in scopes:
                    scopes = {""}
                    warnings.append({"kind": "legacy_workspace_snapshot", "sandbox_id": sandbox_id,
                        "note": "This Run has no task-directory ownership; current files may belong to other tasks."})
                source_directories[sandbox_id] = sorted(scopes)
                shared_inputs = {path for row in selected_rows for path in (saved_binding(services, row, sandbox_id) or {}).get("shared_inputs", [])}
                source_shared_inputs[sandbox_id] = sorted(shared_inputs)
                pending, seen = list(scopes | {str(PurePosixPath(path).parent) for path in shared_inputs}), 0
                while pending:
                    directory = pending.pop()
                    cursor = ""
                    while True:
                        try:
                            page = await file_op(sandbox_id, "list", path=directory, cursor=cursor)
                        except PermissionDeniedError:
                            raise
                        except (OSError, ValueError, RuntimeError, DomainError) as error:
                            warnings.append({"kind": "directory_unavailable", "sandbox_id": sandbox_id, "path": directory, "error": str(error)})
                            break
                        for entry in page["entries"]:
                            try:
                                path = "/".join(parts(entry["path"]))
                            except (ValueError, RuntimeError) as error:
                                warnings.append({"kind": "unsupported_file_path", "sandbox_id": sandbox_id, "path": entry["path"], "error": str(error)})
                                continue
                            in_task = any(not scope or path == scope or path.startswith(scope + "/") for scope in scopes)
                            if not in_task and (entry["directory"] or path not in shared_inputs):
                                continue
                            seen += 1
                            if seen > MAX_ENTRIES:
                                warnings.append({"kind": "entry_limit", "sandbox_id": sandbox_id, "limit": MAX_ENTRIES})
                                pending.clear()
                                break
                            if entry.get("blocked"):
                                warnings.append({"kind": "blocked_file", "sandbox_id": sandbox_id, "path": path})
                                continue
                            if entry["directory"]:
                                if not directory and re.fullmatch(r"oaw-run-review-[a-f0-9]{32}(?:-report)?", path):
                                    warnings.append({"kind": "previous_review_export_excluded", "sandbox_id": sandbox_id, "path": path})
                                else:
                                    pending.append(path)
                                continue
                            target = archive / "sandbox files" / sandbox_id / "workspace" / path
                            try:
                                metadata = (await file_op(sandbox_id, "capture_manifest", paths=[path]))[0]
                                if size + metadata["size"] > services.resources.artifacts.max_version_bytes:
                                    warnings.append({"kind": "existing_bundle_size_limit", "sandbox_id": sandbox_id, "path": path})
                                    continue
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with target.open("xb") as output:
                                    offset = 0
                                    while offset < metadata["size"] or offset == 0:
                                        chunk = await file_op(sandbox_id, "read_chunk", path=path, offset=offset, expected=metadata["signature"])
                                        content = base64.b64decode(chunk["data"], validate=True)
                                        if len(content) > CHUNK_BYTES or offset + len(content) > metadata["size"] or not content and offset < metadata["size"]:
                                            raise ResourceValidationError("Source changed during capture")
                                        output.write(content)
                                        offset += len(content)
                                        if offset == metadata["size"]:
                                            break
                                size += offset
                                files.append({"sandbox_id": sandbox_id, "path": str(target.relative_to(archive)), "size": offset,
                                    "provenance": "collection_snapshot", "run_relationship": "not_proven_by_workspace_presence"})
                            except PermissionDeniedError:
                                raise
                            except (OSError, ValueError, RuntimeError, DomainError) as error:
                                target.unlink(missing_ok=True)
                                warnings.append({"kind": "file_unavailable", "sandbox_id": sandbox_id, "path": path, "error": str(error)})
                        if seen > MAX_ENTRIES or not page.get("next_cursor"):
                            break
                        cursor = page["next_cursor"]
        else:
            warnings.append({"kind": "sandbox_files_not_requested"})
        task = {"task_id": selected.task_id, "selected_run_id": selected.run_id, "agent_id": capability.target_id,
                "attempts": attempts, "grouping": "same Agent and explicit continuation_run_id/native task_id only; no prompt-similarity inference"}
        (archive / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2) + "\n")
        (archive / "collection.json").write_text(json.dumps({"collected_at": collected_at, "files": files, "warnings": warnings,
            "source_sandboxes": sandboxes, "source_directories": source_directories, "shared_inputs": source_shared_inputs, "snapshot_scope": "Individually stable current files; not an atomic workspace or historical version snapshot."}, ensure_ascii=False, indent=2) + "\n")
        # Same native chunk boundary as artifact materialization; large model
        # archives do not go through the 16 MiB preview/copy endpoint or the LLM.
        prefix_parts = parts(prefix)
        for length in range(len(parts(destination_workspace["directory"])) + 1, len(prefix_parts)):
            await file_op(request.sandbox_id, "mkdir", path="/".join(prefix_parts[:length]))
        await file_op(request.sandbox_id, "transfer_mkdir", path=prefix)
        directories = {"/".join(prefix_parts[:length]) for length in range(1, len(prefix_parts) + 1)}
        for source in sorted(archive.rglob("*")):
            if not source.is_file():
                continue
            relative = str(source.relative_to(archive)).replace("\\", "/")
            components = parts(prefix + "/" + relative)
            for length in range(1, len(components)):
                directory = "/".join(components[:length])
                if directory not in directories:
                    await file_op(request.sandbox_id, "transfer_mkdir", path=directory)
                    directories.add(directory)
            offset = 0
            with source.open("rb") as stream:
                while True:
                    content = stream.read(CHUNK_BYTES)
                    if not content and offset:
                        break
                    await file_op(request.sandbox_id, "write_chunk", path=prefix + "/" + relative,
                                  offset=offset, data=base64.b64encode(content).decode())
                    offset += len(content)
                    if not content:
                        break
    authorize(services, capability, action)
    services.capabilities.require_sandbox_execute(capability.agent_id, request.sandbox_id)
    return {"agent_id": capability.target_id, "selected_run_id": selected.run_id, "run_ids": [r.run_id for r in selected_rows],
        "sandbox_id": request.sandbox_id, "archive_path": archive_relative, "archive_workspace_path": prefix, "suggested_output_folder": report_relative, "state": "exported", "collected_files": len(files),
        "warning_count": len(warnings), "warnings": warnings[:10], "collection_manifest": archive_relative + "/collection.json",
        "next": "Use log_review inspect_archive, then build_review once in suggested_output_folder with brief summaries of key structure changes. Deliver structure files/previews and trajectory CSV/JSON. Routine reads need no separate annotation; missing versions remain missing."}


def register_run_history(registration):
    async def list_runs(context, capability, arguments):
        return await context.run_history_action(capability, "list", arguments)
    async def export(context, capability, arguments):
        return await context.run_history_action(capability, "export", arguments)
    registration.register_capability(CapabilityDefinition(kind=LIST, tool_name="list_agent_runs", target_parameter="agent", read_only=True,
        description="List this connected Agent's root Runs newest first. Native task_id and explicit continuation_run_id link attempts; similar prompts alone do not. Does not start or message the Agent.",
        input_schema=RunList.model_json_schema()), list_runs)
    schema = RunExport.model_json_schema()
    schema["properties"].pop("sandbox_id")
    schema["required"].remove("sandbox_id")
    registration.register_capability(CapabilityDefinition(kind=EXPORT, tool_name="export_agent_run", target_parameter="agent",
        description="Automatically collect the connected Agent's saved Run logs, full available model requests, thinking, tools, command receipts and conversation messages into a new directory in an independently authorized Sandbox. Omit run_id for latest. Current files are copied only from independently connected source Sandboxes; missing access/files are reported. Does not run archived code, contact the Agent, or change its task.",
        input_schema=schema, selectors=(CapabilitySelector(parameter="sandbox", argument="sandbox_id", capability_kinds=frozenset({"sandbox.execute"}), target_traits=frozenset({"core.sandbox"})),)), export)
