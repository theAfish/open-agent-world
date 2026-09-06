"""Capture and invoke plugin-owned subgraph templates using the existing Run host."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4

from backend.errors import ConflictError, PermissionDeniedError, ResourceValidationError, RevisionConflictError, RuntimeUnavailableError
from backend.legions.models import LegionInstantiate, LegionRecord
from backend.node_documents import read_document, write_document
from backend.plugins.summoning import CallableTemplate, SharedBinding, SummoningPolicy
from backend.runs.models import TERMINAL_RUN_STATUSES
from backend.world.models import CardPatch


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

    def templates(self, node_id):
        spec = self.spec(node_id)
        value = read_document(self.services, node_id)["value"]
        items = value[spec.templates_field] if spec.templates_field else [{**value, "node_id": node_id}]
        return [CallableTemplate.model_validate(item) for item in items]

    def describe(self, item):
        return {"id": item.node_id or item.id, "name": item.name, "description": item.description,
                "entry_agent_key": item.entry_agent_key,
                "nodes": [{"key": n.key, "name": n.name, "type": n.type} for n in item.blueprint.nodes] if item.blueprint else [],
                "shared_bindings": [b.model_dump() for b in item.bindings]}

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
                "templates": [self.describe(item) for item in self.templates(node_id)],
                "instances": [self.view(r) for r in self.records() if r["library_id"] == node_id
                              and (capability is None or r["caller_agent_id"] == capability.agent_id)]}

    async def capture(self, node_id, request):
        services = self.services
        async with services._portable_state_gate.capture():
            async with services._node_mutation():
                spec = self.spec(node_id)
                if not spec.templates_field:
                    raise ResourceValidationError("Save templates into a library")
                current = read_document(services, node_id)
                if current["revision"] != request.expected_revision:
                    raise RevisionConflictError("The library changed. Reload before saving.")
                blueprint, keys = await services._capture_subgraph_locked(request)
                if node_id in keys or set(keys) & set(request.shared_node_ids):
                    raise ResourceValidationError("A shared library or resource cannot also be copied into the template")
                if request.entry_agent_id not in keys or "core.agent" not in services.plugins.node_type(services.world.get_card(request.entry_agent_id).type).traits:
                    raise ResourceValidationError("Choose an entry Agent inside the copied subgraph")
                bindings = []
                for edge in services.world.list_edges():
                    internal = edge.source if edge.source in keys else edge.target if edge.target in keys else None
                    external = edge.target if internal == edge.source else edge.source
                    if internal is None or external not in request.shared_node_ids:
                        continue
                    node = services.world.get_card(external)
                    if not services.plugins.relationship(edge.relationship).templateable:
                        raise ResourceValidationError("This shared relationship does not support templates")
                    bindings.append(SharedBinding(internal_key=keys[internal], internal_is_source=edge.source == internal,
                        external_id="$library" if external == node_id else external,
                        external_type=node.type, external_plugin_id=services.plugins.node_type_owner_id(node.type),
                        relationship=edge.relationship, plugin_id=services.plugins.relationship_owner_id(edge.relationship), direction=edge.direction))
                item = CallableTemplate(name=request.name, description=request.description, blueprint=blueprint,
                    entry_agent_key=keys[request.entry_agent_id], bindings=bindings,
                    policy=SummoningPolicy.model_validate(current["value"].get("policy", {})))
                value = current["value"]
                value[spec.templates_field].append(item.model_dump(mode="json"))
                write_document(services, node_id, value, current["revision"])
                return self.snapshot(node_id)

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
                owned = {key for member in family for key in member["node_ids"]}
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
        async with services._node_mutation():
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
                item = next((t for t in self.templates(node_id) if (t.node_id or t.id) == request.template_id), None)
                if item is None or item.blueprint is None:
                    raise ResourceValidationError("Choose an available template ID from this connection")
                entry = next((n for n in item.blueprint.nodes if n.key == item.entry_agent_key), None)
                if entry is None or "core.agent" not in services.plugins.node_type(entry.type).traits:
                    raise ResourceValidationError("The template needs an entry Agent")
                bindings = [b.model_dump() for b in item.bindings]
                library = services.world.get_card(node_id)
                for binding in bindings:
                    if binding["external_id"] == "$library":
                        target = services.world.get_card(library.parent_id) if not self.spec(node_id).templates_field and library.parent_id else library
                        binding.update(external_id=target.id, external_type=target.type,
                                       external_plugin_id=services.plugins.node_type_owner_id(target.type))
                now = datetime.now(timezone.utc)
                portable = LegionRecord(id=item.id, name=item.name, description=item.description, blueprint=item.blueprint,
                                        created_at=now, updated_at=now, revision=1)
                owned_ids = {key for r in self.records() if r["library_id"] == node_id and not r["reclaimed"] for key in r["node_ids"]}
                previous = [node for node in services.world.list_cards() if node.id in owned_ids]
                space = [library, *services.world.descendants(library.id)]
                instance = await services.instantiate_legion(item.id, LegionInstantiate(position={
                    "x": max(node.position.x + node.size.width for node in space) + 140,
                    "y": max([library.position.y, *[node.position.y + node.size.height + 100 for node in previous]]),
                }), record=portable, bindings=bindings)
                record = {"id": str(uuid4()), "library_id": node_id, "template_id": request.template_id,
                          "name": item.name, "caller_agent_id": capability.agent_id if capability else None,
                          "parent_instance_id": owner["id"] if owner else None,
                          "entry_agent_id": instance.node_ids[item.entry_agent_key],
                          "node_ids": [n.id for n in instance.nodes], "attempts": [],
                          "root_policy": policy, "created_root_id": root_id, "reclaimed": False, "stopping": False}
                self.save(record)
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
