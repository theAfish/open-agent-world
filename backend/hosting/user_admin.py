"""Administrator user management endpoints for hosted installations."""
from __future__ import annotations

import asyncio
import secrets
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .accounts import AccountConflict, normalize_register_username, validate_user_password
from .deployments import read_body

_PENDING_JOB_STATES = frozenset({"queued", "preparing", "preparing_resources", "waiting_resources"})


class AdminCreateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=2, max_length=32)
    display_name: str | None = Field(default=None, max_length=128)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=32)
    password: str = Field(min_length=8, max_length=1024)
    role: Literal["user", "admin"] = "user"


class AdminUpdateUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    role: Literal["user", "admin"] | None = None
    display_name: str | None = Field(default=None, max_length=128)


class UserAdmin:
    def __init__(self, gateway, authorize):
        self.gateway = gateway
        self.authorize = authorize

    def _serialize_user(self, user: dict, entries: dict, pending_users: set[str]) -> dict:
        profile = entries.get(user["id"])
        return {
            "id": user["id"],
            "username": user["username"],
            "display_name": user["display_name"],
            "email": user.get("email"),
            "phone_masked": user.get("phone_masked"),
            "role": user["role"],
            "enabled": user["enabled"],
            "created_at": user["created_at"],
            "last_login_at": user.get("last_login_at"),
            "instance": profile.public() if profile else None,
            "provisioning": user["id"] in pending_users,
        }

    def _pending_user_ids(self) -> set[str]:
        return {
            job["user_id"]
            for job in self.gateway.deployments.jobs()
            if job.get("status") in _PENDING_JOB_STATES
        }

    async def collection(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        await self.gateway.refresh()
        if request.method == "GET":
            q = request.query_params.get("q", "")
            status = request.query_params.get("status", "all")
            if status not in {"all", "enabled", "disabled"}:
                return self.gateway.error(422, "invalid_filter", "Status must be all, enabled, or disabled.")
            enabled_filter = None if status == "all" else (status == "enabled")
            users = await run_in_threadpool(
                lambda: self.gateway.auth.store.list_users(query=q, enabled=enabled_filter)
            )
            entries = self.gateway.profiles
            pending = self._pending_user_ids()
            return JSONResponse({
                "users": [self._serialize_user(user, entries, pending) for user in users]
            })

        actor = await self.gateway.auth.user_from_scope(request.scope)
        try:
            edit = await read_body(request, AdminCreateUserRequest)
            store = self.gateway.auth.store
            username = normalize_register_username(edit.username)
            validate_user_password(edit.password)
            await asyncio.wait_for(self.gateway.auth._password_slots.acquire(), timeout=5)
            try:
                created = await run_in_threadpool(
                    lambda: store.create_user(
                        username,
                        edit.display_name or username,
                        edit.password,
                        role=edit.role,
                        email=edit.email.strip() if edit.email and edit.email.strip() else None,
                        phone=edit.phone.strip() if edit.phone and edit.phone and edit.phone.strip() else None,
                    )
                )
            finally:
                self.gateway.auth._password_slots.release()
        except AccountConflict as exc:
            return self.gateway.error(409, exc.code, str(exc))
        except TimeoutError:
            return self.gateway.error(429, "throttled", "Too many concurrent account operations; retry shortly.")
        except (ValueError, ValidationError) as exc:
            return self.gateway.error(422, "invalid_user", str(exc))

        if created["role"] == "user":
            await self.gateway.auto_provision_user(created, actor_id=actor["id"] if actor else None)
        await self.gateway.refresh()
        return JSONResponse(
            {"user": self._serialize_user(created, self.gateway.profiles, self._pending_user_ids())},
            status_code=201,
        )

    async def item(self, request):
        denied = await self.authorize(request)
        if denied is not None:
            return denied
        await self.gateway.refresh()
        target_id = request.path_params.get("user_id", "")
        try:
            if str(UUID(target_id)) != target_id:
                raise ValueError("Invalid user ID")
            edit = await read_body(request, AdminUpdateUserRequest)
        except (ValueError, ValidationError) as exc:
            return self.gateway.error(422, "invalid_update", str(exc))

        actor = await self.gateway.auth.user_from_scope(request.scope)
        if actor and target_id == actor["id"]:
            if edit.enabled is False or (edit.role is not None and edit.role != "admin"):
                return self.gateway.error(
                    409, "self_modification_rejected", "Cannot disable or demote your own administrator account."
                )

        store = self.gateway.auth.store
        try:
            existing = await run_in_threadpool(store.get_user, target_id)
            if existing is None:
                return self.gateway.error(404, "unknown_user", "User not found.")
            if edit.enabled is not None and edit.enabled != existing["enabled"]:
                await run_in_threadpool(store.set_enabled, target_id, edit.enabled)
            if edit.role is not None and edit.role != existing["role"]:
                await run_in_threadpool(store.set_role, target_id, edit.role)
            if "display_name" in edit.model_fields_set:
                await run_in_threadpool(lambda: store.update_profile(target_id, display_name=edit.display_name))
            updated = await run_in_threadpool(store.get_user, target_id)
        except ValueError as exc:
            return self.gateway.error(422, "invalid_update", str(exc))

        return JSONResponse({
            "user": self._serialize_user(updated, self.gateway.profiles, self._pending_user_ids())
        })
