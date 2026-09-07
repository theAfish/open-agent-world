"""Instantiate live configured Agents using the existing portable graph and Run host."""
from dataclasses import dataclass
from contextlib import nullcontext
from datetime import datetime, timezone
import json
from uuid import uuid4

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RuntimeUnavailableError
from backend.legions.models import LegionInstantiate, LegionRecord
from backend.node_documents import read_document
from backend.plugins.summoning import SummoningPolicy
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.world.models import CardCreate, CardPatch, EdgeCreate, Point
from backend.world.layout import WorldLayout
from backend.events import EventType


@dataclass
class SummoningService:
    services: object

    def spec(self, node_id):
        node = self.services.world.get_card(node_id)
        spec = self.services.plugins.node_type(node.type).summoning
        if spec is None:
            raise ResourceValidationError("This node does not provide callable templates")
        return spec

    def authorize(self, node_id, capability):
        spec = self.spec(node_id)
        if capability is not None:
            live = self.services.capabilities.capability_for_id(capability.agent_id, capability.id)
            if live.target_id != node_id or live.kind != spec.capability_kind:
                raise PermissionDeniedError("Connect to this library or template to summon from it")
        return spec

    def records(self):
        with self.services.database.locked() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT record_json FROM summoned_instances ORDER BY rowid")]

    def save(self, record):
        with self.services.database.transaction() as connection:
            connection.execute("INSERT INTO summoned_instances VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET record_json=excluded.record_json",
                               (record["id"], json.dumps(record)))

    def owned_ids(self, record):
        """Current private ownership, including equipment attached after admission."""
        world = self.services.world
        owned = {node.id for key in record["root_node_ids"] if world.maybe_get_card(key)
                 for node in [world.get_card(key), *world.owned_descendants(key)]}
        if record.get("workspace_id") and world.maybe_get_card(record["workspace_id"]):
            owned.add(record["workspace_id"])
        return owned

    def agents(self, node_id):
        self.spec(node_id)
        return [node for node in self.services.world.list_members(node_id)
                if "core.agent" in self.services.plugins.node_type(node.type).traits]

    def describe(self, agent):
        return {"id": agent.id, "name": agent.name,
                "description": agent.config.get("description", ""),
                "equipment_count": len(self.services.world.equipment_for(agent.id))}

    async def definition(self, agent):
        # An in-memory portable snapshot of the live Agent, never a saved second definition.
        from backend.legions.models import LegionCapture
        request = LegionCapture.model_construct(name=agent.name, description="", node_ids=[agent.id])
        blueprint, keys = await self.services._capture_subgraph_locked(request)
        bindings = []
        for edge in self.services.world.list_edges():
            if self.services.plugins.relationship(edge.relationship).generated:
                continue
            if (edge.source in keys) == (edge.target in keys):
                continue
            internal_source = edge.source in keys
            internal = edge.source if internal_source else edge.target
            external = self.services.world.get_card(edge.target if internal_source else edge.source)
            if not self.services.plugins.relationship(edge.relationship).templateable:
                raise ResourceValidationError("An external relationship does not support instantiation")
            bindings.append(dict(internal_key=keys[internal], internal_is_source=internal_source,
                external_id=external.id, external_type=external.type,
                external_plugin_id=self.services.plugins.node_type_owner_id(external.type),
                relationship=edge.relationship, plugin_id=self.services.plugins.relationship_owner_id(edge.relationship),
                direction=edge.direction))
        return blueprint, keys[agent.id], bindings

    def view(self, record):
        manager = self.services.run_manager
        runs = [manager.get_run(attempt["run_id"]) for attempt in record["attempts"]]
        return {**record, "status": "reclaimed" if record["reclaimed"] else "failed" if record.get("admission_error") else runs[-1].status.value if runs else "ready",
                "result": manager.final_text(runs[-1].run_id) if runs else "",
                "error": record.get("admission_error") or (runs[-1].error if runs else None),
                "run_id": runs[-1].run_id if runs else None}

    def snapshot(self, node_id, capability=None):
        self.authorize(node_id, capability)
        value = read_document(self.services, node_id)["value"]
        return {"instructions": value.get("instructions", ""), "policy": value.get("policy", {}),
                "agents": [self.describe(item) for item in self.agents(node_id)],
                "instances": [self.view(r) for r in self.records() if r["library_id"] == node_id
                              and (capability is None or r["caller_agent_id"] == capability.agent_id)]}

    def check_budget(self, root_id, policy, *, creating):
        records = [r for r in self.records() if r["created_root_id"] == root_id or any(a["root_run_id"] == root_id for a in r["attempts"])]
        for record in records:
            policies = [a["policy"] for a in record["attempts"] if a["root_run_id"] == root_id]
            if record["created_root_id"] == root_id:
                policies.append(record["root_policy"])
            for admitted in policies:
                policy = {key: min(value, admitted[key]) for key, value in policy.items()}
        if creating and sum(r["created_root_id"] == root_id for r in records) >= policy["max_instances"]:
            raise ConflictError("This root task reached its total summoned instance limit")
        active = sum(self.services.run_manager.get_run(a["run_id"]).status not in TERMINAL_RUN_STATUSES
                     for r in records for a in r["attempts"] if a["root_run_id"] == root_id)
        if active >= policy["max_concurrent"]:
            raise ConflictError("This root task reached its concurrent summon limit")
        return policy

    async def action(self, node_id, request, *, capability=None):
        services = self.services
        manager = services.run_manager
        if request.action == "list":
            return self.snapshot(node_id, capability)
        if request.action in {"stop", "reclaim"}:
            async with services._node_mutation():
                self.authorize(node_id, capability)
                record = self.find_instance(node_id, request.instance_id, capability)
                if record["stopping"]:
                    raise ConflictError("This instance is already stopping")
                family = [record]
                for candidate in self.records():
                    if candidate["parent_instance_id"] in {r["id"] for r in family}:
                        family.append(candidate)
                # Reserve the whole family before joining provider cleanup.
                for member in family:
                    member["stopping"] = True
                    self.save(member)
            try:
                owned = {key for member in family for key in self.owned_ids(member)}
                for node in services.world.list_cards():
                    if node.id in owned and services.node_execution.active(node.id):
                        await services.node_execution.stop(node.id)
                for member in family:
                    for attempt in member["attempts"]:
                        await manager.cancel_run(attempt["run_id"])
                for node in services.world.list_cards():
                    if node.id in owned and "core.agent" in services.plugins.node_type(node.type).traits:
                        await manager.cancel_agent_runs(node.id)
                if request.action == "reclaim":
                    async with services._node_mutation():
                        # Cards the user moved into the instance remain their own resources.
                        for node in services.world.list_cards():
                            if node.parent_id in owned and node.id not in owned:
                                await services.update_card(node.id, CardPatch(parent_id=None))
                        live = [node.id for node in services.world.list_cards() if node.id in owned]
                        if live:
                            await services.delete_cards(live)
                    for member in family:
                        member["reclaimed"] = True
                        self.save(member)
            finally:
                for member in family:
                    member["stopping"] = False
                    self.save(member)
            return self.view(record)
        async with (services._portable_state_gate.capture() if request.action == "summon" else nullcontext()), services._node_mutation():
            self.authorize(node_id, capability)
            if request.action == "inspect":
                return self.view(self.find_instance(node_id, request.instance_id, capability))
            if request.prompt is None or not request.prompt.strip():
                raise ResourceValidationError("Supply a task prompt")
            context = manager.current_context
            if capability is not None and (context is None or context.agent_id != capability.agent_id):
                raise PermissionDeniedError("Summon from an active Agent Run")
            owner = next((r for r in self.records() if context and context.agent_id in r["node_ids"] and not r["reclaimed"]), None)
            if owner and owner["stopping"]:
                raise ConflictError("This instance is stopping; no further summons can start")
            policy = SummoningPolicy.model_validate(read_document(services, node_id)["value"].get("policy", {})).model_dump()
            depth = 1
            parent = manager.get_run(context.run_id) if context else None
            while parent:
                if parent.caller_kind == "summon":
                    depth += 1
                parent = manager.get_run(parent.parent_run_id) if parent.parent_run_id else None
            root_id = context.root_run_id if context else None
            if root_id:
                policy = self.check_budget(root_id, policy, creating=request.action == "summon")
            if depth > policy["max_depth"]:
                raise ConflictError("This root task reached its summon depth limit")
            if request.action == "summon":
                agent = next((a for a in self.agents(node_id) if a.id == request.agent_id), None)
                if agent is None:
                    raise ResourceValidationError("Choose an Agent currently in this Barracks")
                blueprint, entry_key, bindings = await self.definition(agent)
                now = datetime.now(timezone.utc)
                portable = LegionRecord(id=agent.id, name=agent.name, description="", blueprint=blueprint,
                                        created_at=now, updated_at=now, revision=1)
                layout = WorldLayout.capture(services.world)
                library_bounds = layout.footprints[node_id]
                spec = services.plugins.node_type("core.virtual-workspace").container
                preferred = Point(x=library_bounds.x + library_bounds.width + 140, y=library_bounds.y)
                instance = await services.instantiate_legion(agent.id, LegionInstantiate(position={
                    "x": preferred.x + spec.content_inset[0],
                    "y": preferred.y + spec.content_inset[1],
                }), record=portable, bindings=bindings)
                workspace = None
                try:
                    placement = layout.plan_container(instance.nodes, spec, preferred=preferred)
                    workspace = await services._create_card(CardCreate(
                        type="core.virtual-workspace", name=f"{agent.name} workspace",
                        position=placement.position, size=placement.size))
                    for node in instance.nodes:
                        await services.update_card(node.id, CardPatch(
                            position=Point(x=node.position.x + placement.offset.x, y=node.position.y + placement.offset.y),
                            **({"parent_id": workspace.id} if node.id in placement.root_node_ids else {})))
                    source_id = (services.capabilities.capability_for_id(capability.agent_id, capability.id).source_node_id
                                 if capability else node_id)
                    await services.create_edge(EdgeCreate(
                        source=source_id, target=workspace.id, relationship="core.generated"))
                except Exception:
                    await services.delete_cards([n.id for n in instance.nodes] + ([workspace.id] if workspace else []))
                    raise
                record = {"id": str(uuid4()), "library_id": node_id, "agent_id": request.agent_id,
                          "workspace_id": workspace.id,
                          "name": agent.name, "caller_agent_id": capability.agent_id if capability else None,
                          "parent_instance_id": owner["id"] if owner else None,
                          "entry_agent_id": instance.node_ids[entry_key],
                          "root_node_ids": [instance.node_ids[entry_key]],
                          "node_ids": [n.id for n in instance.nodes], "attempts": [],
                          "root_policy": policy, "created_root_id": root_id, "reclaimed": False, "stopping": False}
                self.save(record)
                await services.events.publish(
                    EventType.NODES_GENERATED, node_id=record["entry_agent_id"],
                    payload={"source_id": agent.id, "target_id": record["entry_agent_id"],
                             "container_id": workspace.id,
                             "nodes": [services.get_card(key).model_dump(mode="json")
                                       for key in [workspace.id, *record["node_ids"]]]})
            else:
                record = self.find_instance(node_id, request.instance_id, capability)
                if record["reclaimed"] or record["stopping"]:
                    raise ConflictError("This instance has been reclaimed or is stopping")
                if manager.is_agent_in_lineage(record["entry_agent_id"]):
                    raise ConflictError("Cannot call an instance already in this Run lineage")
            try:
                if context and manager.get_run(context.run_id).status in TERMINAL_RUN_STATUSES:
                    raise RuntimeUnavailableError("The calling Run ended before summon admission")
                run = await manager.start_run(record["entry_agent_id"], request.prompt, caller_kind="summon",
                                              caller_id=capability.agent_id if capability else None)
            except RuntimeUnavailableError as error:
                record["admission_error"] = str(error)
                self.save(record)
                return self.view(record)
            record["admission_error"] = None
            record["attempts"].append({"run_id": run.run_id, "root_run_id": run.root_run_id, "policy": policy})
            record["created_root_id"] = record["created_root_id"] or run.root_run_id
            self.save(record)
        # Human try returns immediately; Agent tools return the completed turn and handle.
        if capability is not None:
            await manager.wait_execution(run.run_id)
        return self.view(record)

    def find_instance(self, node_id, instance_id, capability):
        record = next((r for r in self.records() if r["id"] == instance_id and r["library_id"] == node_id), None)
        if record is None:
            raise ResourceValidationError("Instance not found in this library")
        if capability is not None and record["caller_agent_id"] != capability.agent_id:
            raise PermissionDeniedError("An Agent can only manage instances it summoned")
        return record
