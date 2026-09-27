"""On-demand, read-only checks. Never run agents, tools or repair operations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.dependencies import get_services
from backend.legions.runtime import member_team
from backend.security.model_connections import ModelConnectionStore

router = APIRouter(tags=["system"])


class Check(BaseModel):
    id: str
    status: Literal["ok", "warning", "info"]
    code: str
    node_id: str | None = None
    name: str | None = None
    focus_id: str | None = None
    x: float | None = None
    y: float | None = None


class Diagnostics(BaseModel):
    checked_at: str
    card_count: int
    checks: list[Check]


def _agent_check(services, card, models):
    manager = services.run_manager
    try:
        # Resolves registration only; does not construct a provider or occupy a slot.
        provider = manager._optional_provider_id(card) if manager else None
    except Exception:
        return "warning", "agent_runtime"
    if not provider:
        return "warning", "agent_runtime"
    if provider != "google.adk":
        return "info", "external_agent"
    reference = str(card.config.get("model") or "oaw:default")
    team = member_team(services.world, card)
    if team and card.config.get("inherit_legion_model", True):
        reference = str(team.config.get("model_override") or "").strip() or reference
    if reference == "oaw:default":
        reference = models.read().default_model or ""
    if not reference:
        return "warning", "model_configuration"
    if not reference.startswith("oaw:model:"):
        return "info", "legacy_model"
    try:
        # Includes disabled/missing models, decryption and backend environment keys.
        # The resolved key and provider error text never leave this function.
        models.resolve(reference)
    except Exception:
        return "warning", "model_configuration"
    return "ok", "model_configured"


@router.get("/diagnostics", response_model=Diagnostics)
async def diagnostics(services=Depends(get_services)):
    checks = [Check(id="backend", status="ok", code="backend")]
    sandboxes = []
    async with services._node_mutation(read_only=True):
        # Inspect all persisted cards, including off-screen and nested components.
        cards = services.world.list_cards()
        by_id = {card.id: card for card in cards}
        definitions = {item.id: item for item in services.plugins.catalog().node_types}
        models = ModelConnectionStore(services.llm_settings)
        try:
            environments = {item["id"]: item["state"] for item in services.plugin_bootstrap.records()} if services.plugin_bootstrap else {}
        except Exception:
            environments = {}
            checks.append(Check(id="environments", status="info", code="check_unavailable"))
        edges = services.world.list_edges()
        broken = any(edge.source not in by_id or edge.target not in by_id for edge in edges)
        checks.append(Check(id="connections", status="warning" if broken else "ok",
                            code="broken_connections" if broken else "connections"))
        for card in cards:
            focus = card
            seen = {card.id}
            # Locate the outer container/backpack owner without changing membership.
            while True:
                owner = focus.equipment.owner_id if focus.equipment else focus.parent_id
                if owner not in by_id or owner in seen:
                    break
                seen.add(owner)
                focus = by_id[owner]
            check = Check(id=card.id, node_id=card.id, name=card.name, focus_id=focus.id,
                          x=focus.position.x, y=focus.position.y, status="ok", code="card_configured")
            definition = definitions.get(card.type)
            try:
                if definition is None:
                    check.status, check.code = "warning", "plugin_unavailable"
                elif card.status in {"error", "failed"} or card.config.get("status") in {"error", "failed"}:
                    check.status, check.code = "warning", "card_error"
                elif environments.get(definition.plugin_id) == "environment_failed":
                    check.status, check.code = "warning", "plugin_environment"
                elif environments.get(definition.plugin_id) in {"discovered", "environment_pending", "environment_installing"}:
                    check.status, check.code = "info", "plugin_preparing"
                elif "core.agent" in definition.traits:
                    check.status, check.code = _agent_check(services, card, models)
                elif card.type == "sandbox":
                    sandboxes.append(check)
                    check.status, check.code = "info", "check_unavailable"
                elif card.type == "conversation" and not any(
                    edge.relationship == "participate" for edge in services.world.connections_to(card.id)
                ):
                    check.status, check.code = "info", "conversation_unconnected"
                elif definition.plugin_id != "open-agent-world.core":
                    check.status, check.code = "info", "plugin_unverified"
            except Exception:
                # Isolate a broken component and never return raw secrets/config/errors.
                check.status, check.code = "info", "check_unavailable"
            checks.append(check)

    # Runtime discovery is bounded, does not hold the graph lock and never starts a
    # sandbox. A stopped but available sandbox is normal, not a failed component.
    semaphore = asyncio.Semaphore(4)

    async def inspect_sandbox(check):
        async with semaphore:
            try:
                info = await asyncio.wait_for(services.get_sandbox(check.node_id), timeout=3)
                if not info.available:
                    check.status, check.code = "warning", "sandbox_unavailable"
                elif info.network_enabled and not info.network_available:
                    check.status, check.code = "warning", "sandbox_network"
                elif info.state == "error":
                    check.status, check.code = "warning", "card_error"
                elif info.state == "stopped":
                    check.status, check.code = "info", "sandbox_stopped"
                else:
                    check.status, check.code = "ok", "sandbox_available"
            except Exception:
                check.status, check.code = "info", "check_unavailable"

    try:
        await asyncio.wait_for(asyncio.gather(*(inspect_sandbox(check) for check in sandboxes)), timeout=10)
    except TimeoutError:
        pass  # Remaining rows already say unverified, never healthy.
    return Diagnostics(checked_at=datetime.now(timezone.utc).isoformat(), card_count=len(cards), checks=checks)
