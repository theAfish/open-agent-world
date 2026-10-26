"""Import an authorized host file reference without sending its bytes to the model."""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field

from . import actions
from .errors import KnowledgeError, operator_message


ACTION = "import_artifact"
KIND = f"knowledge.base.{ACTION}"
TOOL_NAME = "knowledge_import_artifact"
DESCRIPTION = (
    "Read a file from an authorized conversation attachment or published artifact collection "
    "into this knowledge base and start converting it to readable markdown. Use the attachment's "
    "version_id and path; source selects its connected conversation or artifact collection. "
    "PDF, text, CSV, TSV, XLSX and supported images use the existing conversion engines. "
    "No Sandbox, re-upload or file bytes are needed. Repeating the same reference in the same "
    "group/upload reuses its source. Omit bundle_id for literature or ordinary document reading; "
    "for experiment files, prepare an upload with knowledge_experiment_bundle_prepare first. "
    "Follow the returned next_step: wait with knowledge_jobs for conversion, then read with "
    "knowledge_markdown or knowledge_document_search. Imported material is source evidence, "
    "not published knowledge."
)


class ArtifactImport(actions.Request):
    source_collection_id: str = Field(min_length=1)
    version_id: UUID
    path: str = Field(min_length=1, max_length=1024)
    group_id: UUID | None = None
    bundle_id: UUID | None = None


class _FileImport(ArtifactImport):
    data: bytes = Field(min_length=1, max_length=actions.MAX_UPLOAD_BYTES)


async def invoke(context, capability, arguments):
    request = ArtifactImport.model_validate(arguments)
    data = await context.read_artifact_file(
        capability, request.source_collection_id, str(request.version_id), request.path,
        max_bytes=actions.MAX_UPLOAD_BYTES,
    )
    # This internal transfer stays inside the host. The public tool only accepts
    # file references, and node_resource_action rechecks the destination grant.
    return await context.node_resource_action(capability, ACTION,
        {**request.model_dump(mode="json"), "data": data})


def store_import(context, arguments):
    if not context.actor_id:
        raise KnowledgeError("Import an attachment through knowledge_import_artifact so its source access is verified")
    request = _FileImport.model_validate(arguments)
    filename = PurePosixPath(request.path).name
    if not filename or "\\" in filename or len(filename) > 255:
        raise KnowledgeError("The attachment must have a valid filename of at most 255 characters")
    bundle_id = str(request.bundle_id) if request.bundle_id else None
    kb = actions._client(context)
    if bundle_id:
        bundle = kb.oaw_experiments.require_bundle(bundle_id)
        if bundle["status"] == "deleted":
            raise KnowledgeError("That experiment upload was deleted")
        group_id = actions._uuid(bundle["group_id"], "group_id")
        if request.group_id is not None and request.group_id != group_id:
            raise KnowledgeError("group_id must match the experiment upload's group")
    else:
        group_id = actions._resolve_group(kb, request.group_id, actions.settings_of(context)).id

    # The existing source store accepts caller-owned IDs. This makes retries and
    # restarts idempotent without a second file store or an in-memory receipt.
    source_id = uuid5(NAMESPACE_URL,
        f"oaw:knowledge:artifact:{group_id}:{bundle_id or 'literature'}:{request.version_id}:{request.path}")
    source = kb.sources.get(source_id)
    reused = source is not None
    digest = hashlib.sha256(request.data).hexdigest()
    membership = kb.oaw_experiments.bundle_of_source(source_id) if bundle_id else None
    if bundle_id and (not reused or membership != bundle_id) and kb.oaw_experiments.list(
            bundle_id=bundle_id, status="published", limit=1):
        raise KnowledgeError("This upload has published records; create a new upload for additional files")
    if source is None:
        source = kb.sources.add_bytes(group_id, request.data, source_id=source_id,
            filename=filename, media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
            metadata={"uploaded_by": actions._actor(context),
                "origin": {"collection_id": request.source_collection_id,
                    "version_id": str(request.version_id), "path": request.path},
                **({"workflow": "experiment", "bundle_id": bundle_id} if bundle_id else {})})
    elif source.sha256 != digest or group_id not in source.collection_ids:
        raise KnowledgeError("The previously imported attachment no longer matches this source")

    if bundle_id:
        if membership is None:
            kb.oaw_experiments.add_bundle_source(bundle_id, source_id)
        elif membership != bundle_id:
            raise KnowledgeError("This source belongs to a different experiment upload")
        if not reused and bundle["status"] != "ready":
            kb.oaw_experiments.update_bundle(bundle_id, status="ready", run_id=None)

    result = {"source": {"id": str(source.id), "filename": source.filename,
                          "size": source.size, "sha256": source.sha256},
              "group_id": str(group_id), "reused": reused}
    if bundle_id:
        result["bundle_id"] = bundle_id
    try:
        conversion = actions.process_sources(context, {"source_ids": [str(source.id)]})["jobs"][0]
    except Exception as error:
        message = operator_message(error)
        if message is None:
            raise
        # Keep the successful import visible so the Agent can retry conversion
        # by source_id instead of re-uploading an already retained file.
        return actions._bounded({**result, "state": "pending", "error": message,
            "next_step": {"tool": "knowledge_process", "source_ids": [str(source.id)]}})
    if conversion.get("skipped") == "already converted":
        return actions._bounded({**result, "state": "ready",
            "next_step": {"tool": "knowledge_markdown", "source_id": str(source.id)}})
    job = conversion["job"]
    state = "ready" if job["status"] == "COMPLETED" else (
        "converting" if job["status"] in actions.ACTIVE_JOBS else "failed")
    return actions._bounded({**result, "state": state, "job": job,
        "next_step": {"tool": "knowledge_jobs", "job_id": job["id"]}})
