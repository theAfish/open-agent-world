"""Admin deployment intents and entry assignments, separate from users' world data."""
from __future__ import annotations

import hashlib
import json
import time
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.responses import JSONResponse

from .configuration import unix_socket_path
from .shared_models import SettingsDatabase
from .worker import mint_worker_token

MAX_TEMPLATE = 4 * 1024 * 1024


class DeploymentStore(SettingsDatabase):
    def __init__(self, path):
        super().__init__(path)
        with self.transaction(immediate=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS hosting_jobs (
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
                    requested_by TEXT NOT NULL, user_id TEXT NOT NULL, profile_id TEXT NOT NULL,
                    mode TEXT NOT NULL, name TEXT NOT NULL, template_json TEXT NOT NULL,
                    status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS hosting_entries (
                    user_id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, changed_by TEXT NOT NULL, changed_at REAL NOT NULL
                );
            """)

    def entries(self):
        with self.locked() as db:
            return {row["user_id"]: row["profile_id"] for row in db.execute("SELECT * FROM hosting_entries")}

    def assign(self, user_id, profile_id, admin_id):
        with self.transaction(immediate=True) as db:
            db.execute("INSERT INTO hosting_entries VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                       "profile_id=excluded.profile_id, changed_by=excluded.changed_by, changed_at=excluded.changed_at",
                       (user_id, profile_id, admin_id, time.time()))

    def jobs(self):
        with self.locked() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,user_id,profile_id,mode,name,status,message,created_at FROM hosting_jobs ORDER BY created_at DESC LIMIT 100")]

    def enqueue(self, request, actor, template):
        encoded = json.dumps(template, ensure_ascii=False)
        if len(encoded.encode()) > MAX_TEMPLATE:
            raise ValueError("Template exceeds 4 MiB")
        digest = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        with self.transaction(immediate=True) as db:
            previous = db.execute("SELECT * FROM hosting_jobs WHERE request_id=?", (str(request.request_id),)).fetchall()
            if previous:
                if any(row["request_digest"] != digest or row["requested_by"] != actor for row in previous):
                    raise ValueError("Request ID already used for a different deployment")
                return [row["id"] for row in previous]
            if db.execute("SELECT COUNT(*) FROM hosting_jobs WHERE status IN ('queued','preparing')").fetchone()[0] + len(request.user_ids) > 10:
                raise ValueError("Wait for pending deployments to finish")
            ids = []
            for target in request.user_ids:
                job_id = str(uuid5(request.request_id, str(target)))
                db.execute("INSERT INTO hosting_jobs VALUES (?,?,?,?,?,?,?,?,?,'queued','',?)",
                           (job_id, str(request.request_id), digest, actor, str(target), job_id,
                            request.mode, template["name"], encoded, time.time()))
                ids.append(job_id)
            return ids

    def previous(self, request, actor):
        digest = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        with self.locked() as db:
            rows = db.execute("SELECT * FROM hosting_jobs WHERE request_id=?", (str(request.request_id),)).fetchall()
            if any(row["request_digest"] != digest or row["requested_by"] != actor for row in rows):
                raise ValueError("Request ID already used for a different deployment")
            return [row["id"] for row in rows]


class DeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    release_id: UUID
    user_ids: list[UUID] = Field(min_length=1, max_length=10)
    mode: str = "runtime"


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    profile_id: UUID


async def read_body(request, model):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise ValueError("Request too large")
    return model.model_validate_json(body)


class DeploymentAdmin:
    def __init__(self, gateway, authorize):
        self.gateway, self.authorize = gateway, authorize

    async def worker(self, profile, path):
        gateway = self.gateway
        client = gateway.unix_clients.get(profile.profile_id, gateway.http)
        origin = "http://localhost" if unix_socket_path(profile.upstream) else profile.upstream
        headers = {"Authorization": "Bearer " + mint_worker_token(profile.secret, profile.profile_id, profile.user_id),
                   "Origin": gateway.settings.public_origin}
        async with client.stream("GET", origin + path, headers=headers) as response:
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > MAX_TEMPLATE:
                    raise ValueError("Published definition is too large")
            value = json.loads(data)
            if not response.is_success:
                error = value.get("error", {})
                raise ValueError(error.get("message") or value.get("detail") or "Workspace is unavailable")
        return value

    async def catalog(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        gateway = self.gateway
        await gateway.refresh()
        user = await gateway.auth.user_from_scope(request.scope)
        profile = gateway.profiles.get(user["id"])
        releases, release_error = [], None
        if profile and profile.mode == "builder":
            try:
                releases = [{k: r[k] for k in ("id", "name", "created_at", "legion_id")}
                            for r in await self.worker(profile, "/api/deployments")]
            except (httpx.HTTPError, ValueError):
                release_error = "Engineering workspace unavailable; retry after it starts."
        entries = gateway.profiles
        return JSONResponse({
            "releases": releases, "release_error": release_error,
            "users": [{**{k: u[k] for k in ("id", "username", "display_name", "role", "enabled")},
                       "entry_id": entries[u["id"]].profile_id if u["id"] in entries else None}
                      for u in gateway.auth.store.list_users()],
            "profiles": [{**p.public(), "user_id": p.user_id} for p in gateway.settings.profiles],
            "jobs": gateway.deployments.jobs(),
        })

    async def deploy(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        gateway = self.gateway
        await gateway.refresh()
        actor = await gateway.auth.user_from_scope(request.scope)
        try:
            edit = await read_body(request, DeployRequest)
            if edit.mode not in {"builder", "runtime"} or len(set(edit.user_ids)) != len(edit.user_ids):
                raise ValueError("Choose a valid mode and unique target accounts")
            for target in edit.user_ids:
                user = gateway.auth.store.get_user(str(target))
                if not user or not user["enabled"] or user["role"] == "admin":
                    raise ValueError("Choose enabled user accounts; the engineering workspace remains separate")
            source = gateway.profiles.get(actor["id"])
            if source is None or source.mode != "builder":
                raise ValueError("Assign an engineering workspace to this administrator first")
            previous = gateway.deployments.previous(edit, actor["id"])
            if previous:
                return JSONResponse({"job_ids": previous}, status_code=202)
            # Freeze portable content now; workers never copy source profile databases.
            template = await self.worker(source, "/api/hosting-template/" + str(edit.release_id))
            from .templates import validate_template
            validate_template(template)
            if template["source_release_id"] != str(edit.release_id):
                raise ValueError("Source release mismatch")
            ids = gateway.deployments.enqueue(edit, actor["id"], template)
            return JSONResponse({"job_ids": ids}, status_code=202)
        except (ValueError, ValidationError) as error:
            return gateway.error(409, "deployment_rejected", str(error))
        except httpx.HTTPError:
            return gateway.error(503, "workspace_unavailable", "Engineering workspace unavailable; retry after it starts.")

    async def assign(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        gateway = self.gateway
        await gateway.refresh()
        actor = await gateway.auth.user_from_scope(request.scope)
        try:
            edit = await read_body(request, AssignRequest)
            target = gateway.auth.store.get_user(str(edit.user_id))
            if not target or not target["enabled"] or target["role"] == "admin":
                raise ValueError("Choose an enabled user account")
            profile = next((p for p in gateway.settings.profiles if p.profile_id == str(edit.profile_id)
                            and p.user_id == str(edit.user_id)), None)
            if profile is None:
                raise ValueError("Workspace does not belong to this account")
            # Assignment preserves old processes, databases and ongoing jobs. It
            # only changes future ingress; old tabs are rejected by instance pinning.
            gateway.deployments.assign(str(edit.user_id), profile.profile_id, actor["id"])
            return JSONResponse({"instance": profile.public()})
        except (ValueError, ValidationError) as error:
            return gateway.error(409, "assignment_rejected", str(error))
