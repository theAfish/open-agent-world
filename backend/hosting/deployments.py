"""Admin deployment intents and entry assignments, separate from users' world data."""
from __future__ import annotations

import hashlib
import json
import time
from uuid import UUID, uuid5

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.responses import JSONResponse

from .configuration import unix_socket_path
from .shared_models import SettingsDatabase
from .worker import mint_worker_token

MAX_TEMPLATE = 4 * 1024 * 1024


def _request_digest(request):
    value = request.model_dump(mode="json")
    if not value.get("target_profiles"):
        value.pop("target_profiles", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


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
            if "operation" not in {row["name"] for row in db.execute("PRAGMA table_info(hosting_jobs)")}:
                db.execute("ALTER TABLE hosting_jobs ADD COLUMN operation TEXT NOT NULL DEFAULT 'initialize'")

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
                "SELECT id,user_id,profile_id,mode,name,status,message,created_at,operation FROM hosting_jobs ORDER BY created_at DESC LIMIT 100")]

    def enqueue(self, request, actor, template):
        encoded = json.dumps(template, ensure_ascii=False)
        if len(encoded.encode()) > MAX_TEMPLATE:
            raise ValueError("Template exceeds 4 MiB")
        digest = _request_digest(request)
        with self.transaction(immediate=True) as db:
            previous = db.execute("SELECT * FROM hosting_jobs WHERE request_id=?", (str(request.request_id),)).fetchall()
            if previous:
                if any(row["request_digest"] != digest or row["requested_by"] != actor for row in previous):
                    raise ValueError("Request ID already used for a different deployment")
                return [row["id"] for row in previous]
            if db.execute("SELECT COUNT(*) FROM hosting_jobs WHERE status IN ('queued','preparing','preparing_resources','waiting_resources')").fetchone()[0] + len(request.user_ids) > 10:
                raise ValueError("Wait for pending deployments to finish")
            ids = []
            for target in request.user_ids:
                job_id = str(uuid5(request.request_id, str(target)))
                profile_id = str(request.target_profiles[target]) if request.target_profiles else job_id
                if db.execute("SELECT 1 FROM hosting_jobs WHERE profile_id=? AND status IN "
                              "('queued','preparing','preparing_resources','waiting_resources') LIMIT 1", (profile_id,)).fetchone():
                    raise ValueError("Wait for the existing profile deployment to finish")
                db.execute("INSERT INTO hosting_jobs "
                           "(id,request_id,request_digest,requested_by,user_id,profile_id,mode,name,template_json,status,message,created_at,operation) "
                           "VALUES (?,?,?,?,?,?,?,?,?,'queued','',?,?)",
                           (job_id, str(request.request_id), digest, actor, str(target), profile_id,
                            request.mode, template["name"], encoded, time.time(),
                            "update" if request.target_profiles else "initialize"))
                ids.append(job_id)
            return ids

    def previous(self, request, actor):
        digest = _request_digest(request)
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
    target_profiles: dict[UUID, UUID] = Field(default_factory=dict, max_length=10)

    @model_validator(mode="after")
    def validate_update_targets(self):
        if self.target_profiles and (self.mode != "runtime" or set(self.target_profiles) != set(self.user_ids)
                                     or len(set(self.target_profiles.values())) != len(self.target_profiles)):
            raise ValueError("Updates require one existing runtime profile for every target account")
        return self


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

    async def worker(self, profile, path, *, request):
        gateway = self.gateway
        client = gateway.unix_clients.get(profile.profile_id, gateway.http)
        origin = "http://localhost" if unix_socket_path(profile.upstream) else profile.upstream
        await gateway.wait_for_worker(profile)
        current = await gateway.auth.user_from_scope(request.scope)
        if current is None:
            return gateway.error(401, "authentication_required", "Sign in to continue.")
        if current["role"] != "admin":
            return gateway.error(403, "admin_required", "Administrator access required.")
        await gateway.refresh()
        if current["id"] != profile.user_id or gateway.profiles.get(current["id"]) != profile:
            return gateway.error(409, "instance_changed", "Reload the engineering workspace.")
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
                published = await self.worker(profile, "/api/deployments", request=request)
                if isinstance(published, JSONResponse):
                    return published
                releases = [{k: r[k] for k in ("id", "name", "created_at", "legion_id")}
                            for r in published]
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
                if edit.target_profiles:
                    profile = next((profile for profile in gateway.settings.profiles
                                    if profile.profile_id == str(edit.target_profiles[target])
                                    and profile.user_id == str(target) and profile.mode == "runtime"), None)
                    if profile is None:
                        raise ValueError("Update target must be an existing runtime belonging to this account")
            source = gateway.profiles.get(actor["id"])
            if source is None or source.mode != "builder":
                raise ValueError("Assign an engineering workspace to this administrator first")
            previous = gateway.deployments.previous(edit, actor["id"])
            if previous:
                return JSONResponse({"job_ids": previous}, status_code=202)
            # Freeze portable content now; workers never copy source profile databases.
            template = await self.worker(source, "/api/hosting-template/" + str(edit.release_id), request=request)
            if isinstance(template, JSONResponse):
                return template
            from .templates import validate_template
            validate_template(template)
            if edit.target_profiles:
                from .template_updates import validate_source_nodes
                from backend.legions.models import LegionBlueprint
                validate_source_nodes(template, LegionBlueprint.model_validate(template["blueprint"]), required=True)
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
