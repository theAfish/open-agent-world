"""Stable projects, safe import previews, and reusable file conversion.

A project is an MKB collection. Its file inventory names the current immutable
source at each relative path; selecting a batch never creates another project.
Import previews are private to their card and actor, and commit receipts survive
restarts so interrupted imports can be resumed without duplicating materials.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import mimetypes
import re
import stat
import tarfile
import threading
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field
from sqlalchemy import Column, Integer, LargeBinary, MetaData, String, Table, Text, delete, or_, select, update

from .actions import (
    ACTIVE_JOBS, MAX_UPLOAD_BYTES, Request, _actor, _bounded, _client,
    _record_link, _uuid, process_sources,
)
from .errors import KnowledgeError
from .pipelines import PIPELINE_NAME

MAX_IMPORT_BYTES = MAX_UPLOAD_BYTES
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_FILES = 1000
MAX_ARCHIVE_ENTRIES = 4000
MAX_EXPANSION_RATIO = 200
PREVIEW_LIFETIME = timedelta(hours=24)
GROUPING = Literal["top", "leaf", "single", "depth", "files"]
_IMPORT_LOCK = threading.RLock()
_NAMESPACE = uuid.UUID("1d3c2439-65a3-4605-8d18-20ca998b5f92")
_METADATA = MetaData()
_FILES = Table(
    "oaw_project_file", _METADATA,
    Column("project_id", String(36), primary_key=True),
    Column("relative_path", String(1024), primary_key=True),
    Column("source_id", String(36), nullable=False),
)
_IMPORTS = Table(
    "oaw_project_import", _METADATA,
    Column("token", String(36), primary_key=True),
    Column("actor", String(255), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("plan", Text),
    Column("result", Text),
)
_UPLOADS = Table(
    "oaw_project_import_file", _METADATA,
    Column("token", String(36), primary_key=True),
    Column("id", String(64), primary_key=True),
    Column("relative_path", String(1024), nullable=False),
    Column("media_type", String(200), nullable=False),
    Column("size", Integer, nullable=False),
    Column("data", LargeBinary, nullable=False),
)


def _schema(kb):
    _METADATA.create_all(kb.oaw_engine)


def _path(value):
    value = str(value).replace("\\", "/")
    parts = value.split("/")
    if (not value or value.startswith("/") or len(value) > 1024
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 for char in value) or ":" in value
            or len(parts) > 64):
        raise KnowledgeError("File paths must be safe relative paths without parent traversal")
    return value


def ensure_project(kb, name, project_id=None, *, description="", collection_id=None):
    if project_id is not None:
        return kb.collections.require(_uuid(project_id, "project_id"))
    if collection_id is not None:
        existing = kb.collections.get(_uuid(collection_id, "collection_id"))
        if existing is not None:
            return existing
    name = str(name).strip()
    if not name or len(name) > 200:
        raise KnowledgeError("Project name must contain 1 to 200 characters")
    return kb.collections.create(name=name, collection_id=collection_id,
                                 metadata={"description": description})


def register_project_file(kb, project_id, source, relative_path=None):
    """Activate an immutable source revision at one path within its project."""
    project_id = _uuid(project_id, "project_id")
    kb.collections.require(project_id)
    if project_id not in source.collection_ids:
        raise KnowledgeError("Source does not belong to this project")
    path = _path(relative_path or (source.metadata or {}).get("relative_path") or source.filename)
    _schema(kb)
    with kb.oaw_engine.begin() as connection:
        connection.execute(delete(_FILES).where(
            _FILES.c.project_id == str(project_id), _FILES.c.relative_path == path))
        connection.execute(_FILES.insert().values(project_id=str(project_id), relative_path=path,
                                                   source_id=str(source.id)))


def project_sources(kb, project_id):
    """Current project files only; earlier source revisions remain evidence."""
    identifier = _uuid(project_id, "project_id")
    kb.collections.require(identifier)
    _schema(kb)
    with kb.oaw_engine.connect() as connection:
        rows = connection.execute(select(_FILES).where(
            _FILES.c.project_id == str(identifier)).order_by(_FILES.c.relative_path)).mappings().all()
    return [kb.sources.require(row["source_id"]) for row in rows]


def project_source_ids(kb, project_id=None):
    """Active file IDs for project-scoped or knowledge-base-wide reads/search."""
    _schema(kb)
    statement = select(_FILES.c.source_id)
    if project_id is not None:
        identifier = _uuid(project_id, "project_id")
        kb.collections.require(identifier)
        statement = statement.where(_FILES.c.project_id == str(identifier))
    with kb.oaw_engine.connect() as connection:
        return set(connection.execute(statement).scalars())


def _conversion_jobs(kb, source_ids):
    latest = {}
    offset = 0
    while source_ids - latest.keys():
        jobs = kb.jobs.list(limit=200, offset=offset)
        for job in jobs:
            source_id = (job.inputs or {}).get("source_id")
            if job.pipeline_name == PIPELINE_NAME and source_id in source_ids:
                latest.setdefault(source_id, job)
        if len(jobs) < 200:
            break
        offset += len(jobs)
    return latest


def _project_json(kb, project, *, details=False):
    sources = project_sources(kb, project.id)
    jobs = _conversion_jobs(kb, {str(source.id) for source in sources})
    files = []
    for source in sources:
        artifact = next((item for item in kb.artifacts.list(source_id=source.id, limit=100)
                         if item.processing_type == "MARKDOWN"), None)
        link = _record_link(kb, artifact.id) if artifact is not None else None
        job = jobs.get(str(source.id))
        state = ("converting" if job and job.status in ACTIVE_JOBS else
                 "failed" if job and job.status in {"FAILED", "CANCELLED"} else
                 "converted" if link is not None else "pending")
        files.append({
            "id": str(source.id), "filename": source.filename,
            "relative_path": (source.metadata or {}).get("relative_path", source.filename),
            "media_type": source.media_type, "size": source.size, "state": state,
            "job_id": str(job.id) if job else None, "error": job.error if job and state == "failed" else None,
            "record_id": str(link.output_id) if link else None,
            "markdown": {"artifact_id": str(artifact.id), "size": artifact.size,
                         "engine": (artifact.metadata or {}).get("engine")} if link else None,
        })
    converted = sum(file["state"] == "converted" for file in files)
    state = ("converting" if any(file["state"] == "converting" for file in files) else
             "failed" if any(file["state"] == "failed" for file in files) else
             "ready" if files and converted == len(files) else "pending")
    result = {"id": str(project.id), "name": project.name,
              "description": (project.metadata or {}).get("description", ""),
              "source_count": len(files), "converted_count": converted,
              "state": state, "created_at": project.created_at, "updated_at": project.updated_at}
    if details:
        result["files"] = files
    return result


class Projects(Request):
    project_id: str | None = None
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


def projects(context, arguments):
    request = Projects.model_validate(arguments)
    kb = _client(context)
    if request.project_id:
        project = _project_json(kb, kb.collections.require(_uuid(request.project_id, "project_id")), details=True)
        return _bounded({"project": project, "projects": [project]})
    page = kb.collections.list(limit=request.limit + 1, offset=request.offset)
    has_more = len(page) > request.limit
    return _bounded({"projects": [_project_json(kb, item) for item in page[:request.limit]],
                     "offset": request.offset, "has_more": has_more,
                     "next_offset": request.offset + request.limit if has_more else None})


class ProjectManage(Request):
    operation: Literal["create", "update"]
    project_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)


def project_manage(context, arguments):
    request = ProjectManage.model_validate(arguments)
    kb = _client(context)
    if request.operation == "create":
        if not request.name or request.project_id:
            raise KnowledgeError("Creating a project requires a name and no project_id")
        project = ensure_project(kb, request.name, description=request.description or "")
    else:
        if not request.project_id:
            raise KnowledgeError("Updating a project requires project_id")
        project = kb.collections.require(_uuid(request.project_id, "project_id"))
        name = request.name.strip() if request.name is not None else project.name
        if not name:
            raise KnowledgeError("Project name must not be empty")
        metadata = dict(project.metadata or {})
        if request.description is not None:
            metadata["description"] = request.description
        project = kb.collections.update(project.id, name=name, metadata=metadata)
    return _bounded({"project": _project_json(kb, project, details=True)})


class ProjectProcess(Request):
    project_ids: list[str] = Field(min_length=1, max_length=200)


def project_process(context, arguments):
    request = ProjectProcess.model_validate(arguments)
    kb = _client(context)
    # Validate every project before submitting any work.
    source_ids = [str(source.id) for project_id in dict.fromkeys(request.project_ids)
                  for source in project_sources(kb, project_id)]
    jobs = []
    for start in range(0, len(source_ids), 100):
        jobs.extend(process_sources(context, {"source_ids": source_ids[start:start + 100]})["jobs"])
    return _bounded({"jobs": jobs})


class ImportFile(Request):
    filename: str = Field(min_length=1, max_length=255)
    relative_path: str | None = Field(default=None, max_length=1024)
    media_type: str | None = Field(default=None, max_length=200)
    content_base64: str = Field(min_length=1)


class ProjectImportPreview(Request):
    files: list[ImportFile] | None = Field(default=None, min_length=1, max_length=MAX_FILES)
    token: str | None = None
    grouping: GROUPING = "top"
    depth: int = Field(default=1, ge=0, le=64)


class ImportProject(Request):
    key: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=200)
    project_id: str | None = None


class ImportAssignment(Request):
    file_id: str
    project_key: str | None = Field(default=None, max_length=1024)


class ProjectImportCommit(Request):
    token: str
    grouping: GROUPING = "top"
    depth: int = Field(default=1, ge=0, le=64)
    projects: list[ImportProject] | None = Field(default=None, max_length=MAX_FILES)
    assignments: list[ImportAssignment] | None = Field(default=None, max_length=MAX_FILES)
    auto_process: bool = True


def _archive_kind(path):
    name = path.lower()
    if name.endswith(".zip"):
        return "zip"
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz")):
        return "tar"
    return None


def _archive_stem(path):
    return re.sub(r"(?i)(\.tar\.(gz|bz2|xz)|\.(zip|tar|tgz|tbz2?|txz))$", "", path)


def _archive_path(name, *, directory=False):
    # Standard `tar ... .` archives use ./ prefixes and a harmless root entry.
    # Parent components, absolute paths and links remain prohibited.
    clean = name.rstrip("/") if directory else name
    while clean.startswith("./"):
        clean = clean[2:]
    if directory and clean == ".":
        return None
    return _path(clean)


def _expand(files):
    expanded = []
    seen = set()
    total = 0

    def add(path, data, media_type=None):
        nonlocal total
        path = _path(path)
        if path in seen:
            raise KnowledgeError(f"Import contains duplicate path: {path}")
        if len(expanded) >= MAX_FILES:
            raise KnowledgeError(f"An import may contain at most {MAX_FILES} files")
        if len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeError("Each imported file is limited to 32 MiB")
        total += len(data)
        if total > MAX_EXPANDED_BYTES:
            raise KnowledgeError("Expanded import exceeds 128 MiB")
        seen.add(path)
        file_key = path + "\0" + hashlib.sha256(data).hexdigest()
        expanded.append({"id": hashlib.sha256(file_key.encode()).hexdigest(), "relative_path": path,
                         "media_type": media_type or mimetypes.guess_type(path)[0] or "application/octet-stream",
                         "size": len(data), "data": data})

    raw_size = sum(len(file["data"]) for file in files)
    if raw_size > MAX_IMPORT_BYTES:
        raise KnowledgeError("An import upload is limited to 32 MiB")
    for file in files:
        path = _path(file.get("relative_path") or file["filename"])
        kind = _archive_kind(path)
        data = file["data"]
        if not kind:
            add(path, data, file.get("media_type"))
            continue
        members = []
        try:
            if kind == "zip":
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    infos = archive.infolist()
                    if len(infos) > MAX_ARCHIVE_ENTRIES:
                        raise KnowledgeError("Archive contains too many entries")
                    for info in infos:
                        member_path = _archive_path(info.filename, directory=info.is_dir())
                        mode = info.external_attr >> 16
                        if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not stat.S_ISREG(mode) and not stat.S_ISDIR(mode)):
                            raise KnowledgeError("Archive links and special files are not allowed")
                        if info.is_dir():
                            continue
                        if info.flag_bits & 1:
                            raise KnowledgeError("Encrypted archives are not supported")
                        if info.file_size > MAX_UPLOAD_BYTES or info.file_size > max(1, info.compress_size) * MAX_EXPANSION_RATIO:
                            raise KnowledgeError("Archive expansion limit exceeded")
                        with archive.open(info) as content:
                            value = content.read(MAX_UPLOAD_BYTES + 1)
                        members.append((member_path, value))
                        if sum(len(value) for _, value in members) + total > MAX_EXPANDED_BYTES:
                            raise KnowledgeError("Expanded import exceeds 128 MiB")
            else:
                with tarfile.open(fileobj=io.BytesIO(data), mode="r|*") as archive:
                    for count, info in enumerate(archive, 1):
                        if count > MAX_ARCHIVE_ENTRIES:
                            raise KnowledgeError("Archive contains too many entries")
                        member_path = _archive_path(info.name, directory=info.isdir())
                        if info.isdir():
                            continue
                        if not info.isfile():
                            raise KnowledgeError("Archive links and special files are not allowed")
                        if info.size > MAX_UPLOAD_BYTES:
                            raise KnowledgeError("Archive expansion limit exceeded")
                        content = archive.extractfile(info)
                        if content is None:
                            raise KnowledgeError("Archive file cannot be read")
                        with content:
                            value = content.read(MAX_UPLOAD_BYTES + 1)
                        members.append((member_path, value))
                        expanded_size = sum(len(value) for _, value in members)
                        if expanded_size + total > MAX_EXPANDED_BYTES or expanded_size > max(1, len(data)) * MAX_EXPANSION_RATIO:
                            raise KnowledgeError("Archive expansion limit exceeded")
        except (zipfile.BadZipFile, tarfile.TarError, RuntimeError, EOFError, OSError) as error:
            raise KnowledgeError(f"Cannot read archive: {error}") from error
        # Preserve a real archive root if there is one; otherwise the archive
        # basename supplies a visible grouping root for loose file collections.
        roots = {name.split("/")[0] for name, _ in members}
        rooted = len(roots) == 1 and all("/" in name for name, _ in members)
        parent = str(PurePosixPath(path).parent)
        for member_path, value in members:
            prefix = (parent + "/" if parent != "." else "") if rooted else _archive_stem(path) + "/"
            add(prefix + member_path, value)
    if not expanded:
        raise KnowledgeError("The import contains no files")
    return expanded


def _group(path, grouping, depth):
    parts = path.split("/")
    if grouping == "single":
        return "__all__", path
    if grouping == "files" or (grouping == "depth" and depth == 0):
        return path, parts[-1]
    count = len(parts) - 1 if grouping == "leaf" else min(depth if grouping == "depth" else 1, len(parts) - 1)
    return "/".join(parts[:count]) or "__root__", "/".join(parts[count:])


def _grouped(files, grouping, depth):
    output, projects_by_key = [], {}
    for file in files:
        key, relative_path = _group(file["relative_path"], grouping, depth)
        item = {name: file[name] for name in ("id", "relative_path", "size")}
        item.update(filename=PurePosixPath(file["relative_path"]).name, project_key=key,
                    project_relative_path=relative_path)
        output.append(item)
        if key not in projects_by_key:
            name = ("Imported project" if key in {"__all__", "__root__"} else
                    PurePosixPath(key).stem if grouping == "files" or depth == 0 else PurePosixPath(key).name)
            projects_by_key[key] = {"key": key, "name": name[:200], "file_count": 0}
        projects_by_key[key]["file_count"] += 1
    return {"files": output, "projects": list(projects_by_key.values()), "grouping": grouping, "depth": depth}


def _load_import(kb, context, token):
    token = str(_uuid(token, "token"))
    _schema(kb)
    with kb.oaw_engine.connect() as connection:
        row = connection.execute(select(_IMPORTS).where(_IMPORTS.c.token == token)).mappings().first()
        if row is None or row["actor"] != _actor(context):
            raise KnowledgeError("Import preview was not found for this actor")
        if not row["plan"] and datetime.fromisoformat(row["created_at"]) + PREVIEW_LIFETIME < datetime.now(timezone.utc):
            raise KnowledgeError("Import preview expired; upload the files again")
        files = connection.execute(select(_UPLOADS).where(_UPLOADS.c.token == token)
                                   .order_by(_UPLOADS.c.relative_path)).mappings().all()
    return dict(row), [dict(file) for file in files]


def preview_files(context, files, *, grouping="top", depth=1, identity=None):
    """Internal byte bridge for authorized conversation attachments."""
    options = ProjectImportPreview.model_validate({"grouping": grouping, "depth": depth})
    expanded = _expand(files)
    kb = _client(context)
    _schema(kb)
    token = str(uuid.uuid5(_NAMESPACE, _actor(context) + ":" + identity)) if identity else str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with kb.oaw_engine.begin() as connection:
        expired = select(_IMPORTS.c.token).where(
            _IMPORTS.c.created_at < (now - PREVIEW_LIFETIME).isoformat(), _IMPORTS.c.plan.is_(None))
        connection.execute(delete(_UPLOADS).where(_UPLOADS.c.token.in_(expired)))
        connection.execute(delete(_IMPORTS).where(_IMPORTS.c.token.in_(expired)))
        existing = connection.execute(select(_IMPORTS).where(_IMPORTS.c.token == token)).mappings().first()
        if existing is not None:
            existing_files = connection.execute(select(_UPLOADS).where(_UPLOADS.c.token == token)).mappings().all()
            if {file["id"] for file in expanded} != {file["id"] for file in existing_files}:
                raise KnowledgeError("Import identity already belongs to different content")
            if existing["plan"]:
                plan = json.loads(existing["plan"])
                options = ProjectImportPreview.model_validate({"grouping": plan["grouping"], "depth": plan["depth"]})
            result = {"token": token, **_grouped(expanded, options.grouping, options.depth)}
            if existing["result"]:
                result["committed"] = True
                result["result"] = {**json.loads(existing["result"]), "reused": True}
            return _bounded(result)
        connection.execute(_IMPORTS.insert().values(token=token, actor=_actor(context), created_at=now.isoformat()))
        connection.execute(_UPLOADS.insert(), [{"token": token, **file} for file in expanded])
    return _bounded({"token": token, **_grouped(expanded, options.grouping, options.depth)})


def project_import_preview(context, arguments):
    request = ProjectImportPreview.model_validate(arguments)
    if bool(request.files) == bool(request.token):
        raise KnowledgeError("Provide uploaded files or an existing preview token")
    if request.token:
        row, files = _load_import(_client(context), context, request.token)
        if row["plan"]:
            raise KnowledgeError("This import is already committed; open its projects")
        return _bounded({"token": row["token"], **_grouped(files, request.grouping, request.depth)})
    files = []
    encoded_size = sum(len(file.content_base64) for file in request.files)
    if encoded_size > ((MAX_IMPORT_BYTES + 2) // 3) * 4:
        raise KnowledgeError("An import upload is limited to 32 MiB")
    for file in request.files:
        if "/" in file.filename or "\\" in file.filename:
            raise KnowledgeError("filename must not contain a path separator")
        try:
            data = base64.b64decode(file.content_base64, validate=True)
        except (binascii.Error, ValueError):
            raise KnowledgeError("content_base64 is not valid base64") from None
        files.append({"filename": file.filename, "relative_path": file.relative_path,
                      "media_type": file.media_type, "data": data})
    return preview_files(context, files, grouping=request.grouping, depth=request.depth)


def project_import_commit(context, arguments):
    request = ProjectImportCommit.model_validate(arguments)
    kb = _client(context)
    # The durable plan and deterministic identifiers make a crash retry safe.
    # The lock also serializes concurrent requests in the embedded/service process.
    with _IMPORT_LOCK:
        row, files = _load_import(kb, context, request.token)
        specification = request.model_dump(exclude={"token"})
        plan_json = json.dumps(specification, sort_keys=True)
        if row["plan"] is not None and row["plan"] != plan_json:
            raise KnowledgeError("This preview already has a different committed import plan")
        if row["result"]:
            return _bounded({**json.loads(row["result"]), "reused": True})
        preview = _grouped(files, request.grouping, request.depth)
        groups = {project["key"]: project for project in preview["projects"]}
        if request.projects is not None:
            if len({project.key for project in request.projects}) != len(request.projects):
                raise KnowledgeError("Project keys must be unique")
            for project in request.projects:
                if not project.name.strip():
                    raise KnowledgeError("Project name must not be empty")
                groups[project.key] = project.model_dump()
        assignments = {file["id"]: file for file in preview["files"]}
        if request.assignments is not None:
            if len({item.file_id for item in request.assignments}) != len(request.assignments):
                raise KnowledgeError("Each file may be assigned only once")
            for assignment in request.assignments:
                if assignment.file_id not in assignments:
                    raise KnowledgeError("Assignment names a file outside this preview")
                assignments[assignment.file_id]["project_key"] = assignment.project_key
        selected = [file for file in files if assignments[file["id"]]["project_key"] is not None]
        if not selected:
            raise KnowledgeError("Select at least one file to import")
        wanted = {assignments[file["id"]]["project_key"] for file in selected}
        if wanted - groups.keys():
            raise KnowledgeError("Assignment names an unknown project")
        # Resolve and validate every target before persisting the plan or files.
        targets = {}
        paths = set()
        for key in wanted:
            target = groups[key]
            existing_id = target.get("project_id")
            identifier = _uuid(existing_id, "project_id") if existing_id else uuid.uuid5(_NAMESPACE, row["token"] + ":" + key)
            if existing_id:
                kb.collections.require(identifier)
            targets[key] = identifier
        for file in selected:
            assignment = assignments[file["id"]]
            path = _path(assignment["project_relative_path"])
            identity = (str(targets[assignment["project_key"]]), path)
            if identity in paths:
                raise KnowledgeError("Two files would use the same path in a project; change their grouping")
            paths.add(identity)
        with kb.oaw_engine.begin() as connection:
            claimed = connection.execute(update(_IMPORTS).where(
                _IMPORTS.c.token == row["token"], or_(_IMPORTS.c.plan.is_(None), _IMPORTS.c.plan == plan_json))
                .values(plan=plan_json).returning(_IMPORTS.c.token)).scalar_one_or_none()
            if claimed is None:
                raise KnowledgeError("This preview already has a different committed import plan")
        created_projects = {}
        source_ids = []
        for key in sorted(wanted):
            identifier = targets[key]
            project = kb.collections.get(identifier)
            if project is None:
                project = ensure_project(kb, groups[key]["name"], collection_id=identifier)
            created_projects[key] = project
        for file in selected:
            assignment = assignments[file["id"]]
            project = created_projects[assignment["project_key"]]
            path = _path(assignment["project_relative_path"])
            digest = hashlib.sha256(file["data"]).hexdigest()
            identifier = uuid.uuid5(project.id, path + ":" + digest)
            source = kb.sources.get(identifier)
            if source is None:
                source = kb.sources.add_bytes(project.id, file["data"], filename=PurePosixPath(path).name,
                    media_type=file["media_type"], source_id=identifier,
                    metadata={"relative_path": path, "uploaded_by": _actor(context)})
            register_project_file(kb, project.id, source, path)
            source_ids.append(str(source.id))
        jobs = project_process(context, {"project_ids": [str(item.id) for item in created_projects.values()]})["jobs"] if request.auto_process else []
        result = _bounded({"projects": [_project_json(kb, item) for item in created_projects.values()],
                           "source_ids": source_ids, "jobs": jobs, "reused": False})
        with kb.oaw_engine.begin() as connection:
            connection.execute(update(_IMPORTS).where(_IMPORTS.c.token == row["token"])
                               .values(result=json.dumps(result)))
            # Keep the manifest for immutable attachment identity reconciliation,
            # while releasing staged bytes as soon as their import is durable.
            connection.execute(update(_UPLOADS).where(_UPLOADS.c.token == row["token"]).values(data=b""))
        return result
