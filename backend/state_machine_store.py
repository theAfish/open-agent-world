"""Host-owned definitions/presentation with runtime values in OAW StateStore.

Definitions are immutable versions. Save creates a draft; apply upgrades the node instance.
Foreign groups are references; hydration for evaluation is a temporary read.
"""
from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4
from pydantic import ValidationError

from backend.errors import ConflictError, NotFoundError, ResourceValidationError, RevisionConflictError
from backend.state_machine import StateMachineConfig, StateMachinePresentation, remap_definition
from backend.state_machine_migrations import consolidate_agent_lifecycle, consolidated_presentation


class StateMachineStore:
    def __init__(self, services):
        self.services = services
        self.database = services.database
        with self.database.transaction(immediate=True) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS state_machine_definitions (
                card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                version INTEGER NOT NULL, definition_json TEXT NOT NULL,
                PRIMARY KEY(card_id,version))""")
            db.execute("""CREATE TABLE IF NOT EXISTS state_machine_editors (
                card_id TEXT PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,
                definition_version INTEGER NOT NULL, revision INTEGER NOT NULL,
                presentation_json TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS state_machine_instances (
                id TEXT PRIMARY KEY, card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                definition_version INTEGER NOT NULL, scope_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                UNIQUE(card_id,scope_key),
                FOREIGN KEY(card_id,definition_version) REFERENCES state_machine_definitions(card_id,version))""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS state_machine_instance_cleanup
                AFTER DELETE ON state_machine_instances BEGIN
                DELETE FROM state_scopes WHERE scope_kind='node_document' AND owner_id='state-machine-' || OLD.id;
                END""")

    def node_instance(self, card_id):
        """One status authority per node, independent of execution/session scopes."""
        with self.database.locked() as db:
            row = db.execute("""SELECT i.id, json_extract(d.definition_json,'$.status_entity_id') AS primary_group FROM state_machine_instances i
                JOIN state_machine_definitions d ON d.card_id=i.card_id AND d.version=i.definition_version
                WHERE i.card_id=? AND i.scope_key='default'""", (card_id,)).fetchone()
        if row and (group := row["primary_group"]):
            return self.get_instance(row["id"]), group
        return None

    def node_status(self, card_id):
        binding = self.node_instance(card_id)
        if not binding:
            return None
        instance, primary = binding
        with self.database.locked() as db:
            row = db.execute("""SELECT json_extract(g.value,'$.id') FROM state_machine_definitions d,
                json_each(d.definition_json,'$.entities') g WHERE d.card_id=? AND d.version=?
                AND json_extract(g.value,'$.ownership')='system'
                ORDER BY CASE WHEN json_extract(g.value,'$.owner')='host.run_manager' THEN 0 ELSE 1 END, g.key
                LIMIT 1""", (card_id, instance["definition_version"])).fetchone()
        operational = row[0] if row else primary
        return instance["states"][operational]

    def state_summary(self, card_id):
        binding = self.node_instance(card_id)
        if not binding:
            return {}
        instance, primary = binding
        definition = self.get_definition(card_id, instance["definition_version"])
        return {"operational_status": self.node_status(card_id), "primary_state": instance["states"][primary],
                "state_groups": {group.id: {"label": group.label, "ownership": group.ownership,
                                            "state_id": instance["states"][group.id]} for group in definition.entities}}

    def node_status_label(self, card_id):
        binding = self.node_instance(card_id)
        if not binding:
            return None
        instance, group_id = binding
        definition = self.get_definition(card_id, instance["definition_version"])
        group = next(entity for entity in definition.entities if entity.id == group_id)
        return next(state.label for state in group.states if state.id == instance["states"][group_id])

    def initialize_node(self, card_id):
        """Install declared node status once. Reading an editor never creates runtime."""
        with self.services.events.committed_batch(), self.database.transaction(immediate=True):
            self._initialize_node(card_id)

    def _merge_system_groups(self, card_id, value, *, disabled=False):
        """One migration boundary for pre-ownership definitions and templates."""
        value = deepcopy(value)
        default = self._default_document(self.services.world.get_card(card_id))
        system = [group for group in (default or {}).get("definition", {}).get("entities", []) if group.get("ownership") == "system"]
        consolidated, aliases = consolidate_agent_lifecycle(value, system)
        if aliases and (protected := self._externally_assigned_groups(card_id)):
            consolidated, aliases = consolidate_agent_lifecycle(value, system, protected)
        value = consolidated
        existing = {group["id"]: group for group in value["entities"]}
        names = dict(aliases)
        for group in system:
            old = existing.get(group["id"])
            if old and old.get("ownership", "user") == "user":
                name = "legacy_" + group["id"]
                while name in existing:
                    name = "legacy_" + name
                names[group["id"]] = name
        def remap(item):
            if isinstance(item, list):
                return [remap(child) for child in item]
            if isinstance(item, dict):
                return {key: names.get(child, child) if key in {"entity_id", "parent_id", "status_entity_id"}
                        else child if key in {"arguments", "references"} else remap(child) for key, child in item.items()}
            return item
        value = remap(value)
        for group in value["entities"]:
            group["id"] = names.get(group["id"], group["id"])
        system_ids = {group["id"] for group in system}
        value["entities"] = system + [group for group in value["entities"] if group["id"] not in system_ids]
        if system and not value.get("status_entity_id"):
            value["status_entity_id"] = system[0]["id"]
        if disabled:
            for rule in value["rules"]:
                rule["enabled"] = False
        return value, names, aliases

    def _dependent_definitions(self, card_id):
        with self.database.locked() as db:
            return db.execute("""SELECT DISTINCT d.card_id,d.version,d.definition_json FROM state_machine_definitions d
                LEFT JOIN state_machine_editors e ON e.card_id=d.card_id AND e.definition_version=d.version
                LEFT JOIN state_machine_instances i ON i.card_id=d.card_id AND i.definition_version=d.version
                WHERE d.card_id<>? AND (e.card_id IS NOT NULL OR i.id IS NOT NULL)""", (card_id,)).fetchall()

    def _externally_assigned_groups(self, card_id):
        # A group with authored external assignments has user semantics, even
        # when its local vocabulary happens to match the old shipped lifecycle.
        protected = set()
        for row in self._dependent_definitions(card_id):
            value = json.loads(row["definition_json"])
            refs = {ref["entity_id"]: ref.get("state_group_id") for ref in value.get("references", []) if ref["card_id"] == card_id}
            for rule in value["rules"]:
                protected.update(refs[effect["entity_id"]] for effect in rule.get("effects", []) if effect["entity_id"] in refs)
            if len(refs) > 1:
                protected.update(refs.values())
        return protected

    def _remap_lifecycle_dependents(self, card_id, aliases):
        aliases = {old: new for old, new in aliases.items() if old != new}
        if not aliases:
            return
        heads, versions = {}, {}
        for row in self._dependent_definitions(card_id):
            value = json.loads(row["definition_json"])
            changed = False
            for ref in value.get("references", []):
                if ref["card_id"] == card_id and ref.get("state_group_id") in aliases:
                    ref["state_group_id"] = aliases[ref["state_group_id"]]
                    changed = True
            if not changed:
                continue
            owner = row["card_id"]
            if owner not in heads:
                heads[owner] = self.get(owner)
            # Only the pointer changes. Keep the owner's active/draft split,
            # enabled flag, user rules and presentation; historical versions stay intact.
            saved = self.save(owner, value, heads[owner]["presentation"], legacy=True)
            versions[owner, row["version"]] = saved["definition_version"]
        with self.database.transaction(immediate=True) as db:
            for owner, head in heads.items():
                db.execute("UPDATE state_machine_editors SET definition_version=? WHERE card_id=?",
                           (versions.get((owner, head["definition_version"]), head["definition_version"]), owner))
            for instance in self.list_instances(False):
                version = versions.get((instance["card_id"], instance["definition_version"]))
                if version is not None:
                    db.execute("UPDATE state_machine_instances SET definition_version=? WHERE id=?", (version, instance["id"]))
                bindings = deepcopy(instance.get("bindings", {}))
                for binding in bindings.values():
                    if binding["entity_id"] in aliases and self.get_instance(binding["instance_id"])["card_id"] == card_id:
                        binding["entity_id"] = aliases[binding["entity_id"]]
                if bindings != instance.get("bindings", {}):
                    self.put_runtime(instance["id"], bindings=bindings)

    def _initialize_node(self, card_id):
        card = self.services.world.get_card(card_id)
        default = self._default_document(card)
        if not default or not default["definition"].get("status_entity_id"):
            return
        bound = self.node_instance(card_id)
        current = self.get(card_id)
        if bound:
            active = self.get_definition(card_id, bound[0]["definition_version"])
            value, names, aliases = self._merge_system_groups(card_id, active.model_dump(mode="json", exclude_none=True))
            candidate = StateMachineConfig.model_validate(value)
            try:
                self.validate_system_groups(card_id, active)
                if not aliases:
                    # An unapplied draft can still contain the old duplicate.
                    if current["definition_version"] != bound[0]["definition_version"]:
                        draft, _, draft_aliases = self._merge_system_groups(card_id, current["definition"])
                        if draft_aliases:
                            self.save(card_id, draft, consolidated_presentation(current["presentation"], draft_aliases, default["presentation"]))
                    return
            except ResourceValidationError:
                pass
            # Contract upgrades are immutable versions; dependencies are checked
            # before changing any instance. User groups and current values survive.
            self._remap_lifecycle_dependents(card_id, aliases)
            self.validate_dependents(card_id, candidate)
            saved = self.save(card_id, candidate, consolidated_presentation(current["presentation"], aliases, default["presentation"]))
            old = bound[0]
            with self.database.transaction(immediate=True) as db:
                db.execute("UPDATE state_machine_instances SET definition_version=? WHERE id=?", (saved["definition_version"], old["id"]))
                states = {group.id: group.initial_state for group in candidate.entities}
                for previous, state in old["states"].items():
                    identity = names.get(previous, previous)
                    group = next((group for group in candidate.entities if group.id == identity), None)
                    if previous != identity and identity in old["states"]:
                        continue  # The existing SYSTEM value wins over a stale copy.
                    if group and state in {item.id for item in group.states}:
                        states[identity] = state
                self.put_runtime(old["id"], states=states, memory={}, _projection=True)
                db.execute("UPDATE state_machine_actions SET status='cancelled',error='System contract changed' WHERE instance_id=? AND status IN ('pending','waiting_capacity')", (old["id"],))
            if current["definition_version"] != old["definition_version"]:
                draft, _, draft_aliases = self._merge_system_groups(card_id, current["definition"])
                self.save(card_id, draft, consolidated_presentation(current["presentation"], draft_aliases, default["presentation"]))
            return
        if not current["definition_version"]:
            saved = self.save(card_id, default["definition"], default["presentation"])
            self.activate(card_id, saved["definition_version"], notify=False, _initializing=True)
            return
        # Existing preview authoring remains a disabled draft. Only the type's
        # own default is initialized; no legacy actions are silently activated.
        draft, _, aliases = self._merge_system_groups(card_id, current["definition"], disabled=True)
        saved = self.save(card_id, draft, consolidated_presentation(current["presentation"], aliases, default["presentation"]), legacy=True)
        with self.database.transaction(immediate=True) as db:
            version = int(db.execute("SELECT COALESCE(MAX(version),0)+1 FROM state_machine_definitions WHERE card_id=?", (card_id,)).fetchone()[0])
            db.execute("INSERT INTO state_machine_definitions VALUES(?,?,?)", (card_id, version, json.dumps(default["definition"])))
        self.activate(card_id, version, notify=False, _initializing=True)

    @staticmethod
    def validate_legacy(definition):
        try:
            StateMachineConfig.model_validate(definition)
        except ValidationError as exc:
            raise ResourceValidationError(f"Invalid state-machine definition: {exc}") from exc

    def has_definition(self, card):
        """Cheap availability projection; never hydrate graphs or create instances."""
        with self.database.locked() as db:
            editor = db.execute("SELECT definition_version FROM state_machine_editors WHERE card_id=?", (card.id,)).fetchone()
        if editor is not None:
            return bool(editor[0])
        definition = self.services.world.card_definition(card)
        return definition is not None and definition.state_machine is not None

    def has_editor(self, card):
        """Editor support is a node type capability, independent of saved data."""
        definition = self.services.world.card_definition(card)
        return definition is not None and (definition.state_machine_editor or definition.state_machine is not None)

    def _default_document(self, card):
        definition = self.services.world.card_definition(card)
        if definition is None or definition.state_machine is None:
            return None
        machine = StateMachineConfig.model_validate(definition.state_machine)
        value = machine.model_dump(mode="json", exclude_none=True)
        positions = {}
        for entity, source in zip(value["entities"], machine.entities):
            entity["card_id"] = card.id
            positions[entity["id"]] = {}
            for index, (state, original) in enumerate(zip(entity["states"], source.states)):
                position = state.pop("position")
                positions[entity["id"]][state["id"]] = position if "position" in original.model_fields_set else {
                    "x": 65 + index % 3 * 220, "y": 110 + index // 3 * 190}
        return {"definition": value, "definition_version": 0, "presentation": {"positions": positions},
                "revision": 0, "enabled": False, "active_definition_versions": []}

    def get(self, card_id, version=None):
        card = self.services.world.get_card(card_id)
        with self.database.locked() as db:
            editor = db.execute("SELECT * FROM state_machine_editors WHERE card_id=?", (card_id,)).fetchone()
            active_versions = [int(row[0]) for row in db.execute(
                "SELECT DISTINCT definition_version FROM state_machine_instances WHERE card_id=? AND enabled=1 ORDER BY definition_version", (card_id,))]
            if (not editor or not editor["definition_version"]) and version is None:
                if not editor and (default := self._default_document(card)) is not None:
                    return default
                return {"definition": None, "definition_version": 0, "presentation": {},
                        "revision": int(editor["revision"]) if editor else 0,
                        "enabled": False, "active_definition_versions": active_versions}
            selected = version if version is not None else int(editor["definition_version"])
            row = db.execute("SELECT definition_json FROM state_machine_definitions WHERE card_id=? AND version=?", (card_id, selected)).fetchone()
        if not row:
            raise NotFoundError("State-machine definition version does not exist")
        return {"definition": json.loads(row[0]), "definition_version": selected,
                "presentation": {key: value for key, value in json.loads(editor["presentation_json"]).items()
                                 if key in StateMachinePresentation.model_fields} if editor else {}, "revision": int(editor["revision"]) if editor else 0,
                "enabled": selected in active_versions, "active_definition_versions": active_versions}

    def get_definition(self, card_id, version=None):
        if version is not None:
            with self.database.locked() as db:
                row = db.execute("SELECT definition_json FROM state_machine_definitions WHERE card_id=? AND version=?", (card_id, version)).fetchone()
            if row is None:
                raise NotFoundError("State-machine definition version does not exist")
            return StateMachineConfig.model_validate_json(row[0])
        result = self.get(card_id, version)
        if result["definition"] is None:
            raise NotFoundError("This object has no state-machine definition")
        return StateMachineConfig.model_validate(result["definition"])

    def validate_system_groups(self, card_id, definition):
        """The live node-type declaration is the authority, never an API payload."""
        default = self._default_document(self.services.world.get_card(card_id))
        declared = StateMachineConfig.model_validate(default["definition"]).entities if default else []
        expected = {group.id: group for group in declared if group.ownership == "system"}
        actual = {group.id: group for group in definition.entities if group.ownership == "system" or group.id in expected}
        def semantic(group):
            value = group.model_dump(mode="json", exclude_none=True)
            value.pop("card_id", None)
            for state in value["states"]:
                state.pop("position", None)
            return value
        if actual.keys() != expected.keys() or any(semantic(actual[key]) != semantic(group) for key, group in expected.items()):
            raise ResourceValidationError("System-owned groups, states, commands and canonical transitions are immutable")

    def _reference_definition(self, card_id):
        bound = self._bound_instance(card_id)
        return self.get_definition(card_id, bound["definition_version"] if bound else None)

    def _bound_instance(self, card_id, scope_key="default"):
        with self.database.locked() as db:
            row = db.execute("SELECT id FROM state_machine_instances WHERE card_id=? AND scope_key=?", (card_id, scope_key)).fetchone()
        return self.get_instance(row[0]) if row else None

    def validate_dependents(self, card_id, candidate):
        """Validate stable addresses before Apply changes an owner's live contract."""
        with self.database.locked() as db:
            rows = db.execute("""SELECT DISTINCT d.card_id,d.definition_json FROM state_machine_definitions d
                LEFT JOIN state_machine_editors e ON e.card_id=d.card_id AND e.definition_version=d.version
                LEFT JOIN state_machine_instances i ON i.card_id=d.card_id AND i.definition_version=d.version
                WHERE e.card_id IS NOT NULL OR i.id IS NOT NULL""").fetchall()
        for row in rows:
            if row["card_id"] == card_id:
                continue
            value = json.loads(row["definition_json"])
            if not any(ref["card_id"] == card_id for ref in value.get("references", [])):
                continue
            try:
                self.resolve_preview_definition(value, replacements={card_id: candidate})
            except (ValueError, ResourceValidationError, NotFoundError) as exc:
                raise ResourceValidationError(f"Apply would break a state reference from {row['card_id']}: {exc}") from exc

    def save(self, card_id, definition, presentation=None, expected_revision=None, *, legacy=False):
        card = self.services.world.get_card(card_id)
        value = (definition.model_dump(mode="json", exclude_none=True) if isinstance(definition, StateMachineConfig)
                 else deepcopy(definition))
        # Decode before removing old presentation fields, so malformed positions
        # and graph references still receive the existing bounded validation.
        value = StateMachineConfig.model_validate(value).model_dump(mode="json", exclude_none=True)
        declared = self.services.world.card_definition(card)
        if not legacy and declared and declared.state_machine and declared.state_machine.status_entity_id:
            value.setdefault("status_entity_id", value["entities"][0]["id"])
        positions = {}
        foreign = {entity["id"]: entity for entity in value["entities"]
                   if entity.get("card_id") and entity["card_id"] != card_id}
        if foreign and not legacy:
            raise ResourceValidationError("Another object's states must be referenced, not copied into this definition")
        if foreign:
            owners = {identity: entity["card_id"] for identity, entity in foreign.items()}
            # A copied member subtree is owned by that member too. Retain only
            # pointers to its groups, including old runtime-placeholder groups.
            for _ in value["entities"]:
                inherited = {entity["id"]: owners[entity["parent_id"]] for entity in value["entities"]
                             if entity.get("parent_id") in owners and entity["id"] not in owners
                             and entity.get("card_id") != card_id}
                if not inherited:
                    break
                owners.update(inherited)
            foreign = {entity["id"]: entity for entity in value["entities"] if entity["id"] in owners}
            references = {ref["entity_id"]: ref for ref in value.get("references", [])}
            references.update({identity: {"entity_id": identity, "card_id": owners[identity], "state_group_id": identity}
                               for identity, entity in foreign.items()})
            value["references"] = list(references.values())
            value["entities"] = [entity for entity in value["entities"] if entity["id"] not in foreign]
        for entity in value["entities"]:
            if entity.get("parent_id") in foreign:
                entity.pop("parent_id", None)
            if entity.get("ownership") == "system":
                entity["card_id"] = card_id
            positions[entity["id"]] = {state["id"]: state.pop("position", {"x": 0, "y": 0}) for state in entity["states"]}
        if not value["entities"]:
            # Old Legion snapshots occasionally consisted solely of member copies.
            local_id = "self"
            reference_ids = {reference["entity_id"] for reference in value["references"]}
            while local_id in reference_ids:
                local_id = "owner_" + local_id
            value["entities"] = [{"id": local_id, "label": "State", "kind": "group", "card_id": card_id,
                                  "initial_state": "initial", "states": [{"id": "initial", "label": "Initial"}]}]
        validated = StateMachineConfig.model_validate(value)
        if not legacy:
            self.validate_system_groups(card_id, validated)
        for rule in value["rules"]:
            if rule["trigger"]["event"] == "unconfigured":
                rule["enabled"] = False
        view = StateMachinePresentation.model_validate(presentation if presentation is not None else {"positions": positions})
        with self.database.transaction(immediate=True) as db:
            current = self.get(card_id)
            if expected_revision is not None and expected_revision != current["revision"]:
                raise RevisionConflictError("State-machine definition changed; read it again before saving")
            version = current["definition_version"]
            if not version or value != current["definition"]:
                version = int(db.execute("SELECT COALESCE(MAX(version),0)+1 FROM state_machine_definitions WHERE card_id=?", (card_id,)).fetchone()[0])
                db.execute("INSERT INTO state_machine_definitions(card_id,version,definition_json) VALUES(?,?,?)",
                           (card_id, version, json.dumps(value)))
            db.execute("""INSERT INTO state_machine_editors(card_id,definition_version,revision,presentation_json)
                       VALUES(?,?,?,?) ON CONFLICT(card_id) DO UPDATE SET
                       definition_version=excluded.definition_version,revision=excluded.revision,presentation_json=excluded.presentation_json""",
                       (card_id, version, current["revision"] + 1, view.model_dump_json(exclude_none=True)))
        return self.get(card_id)

    def clear(self, card_id):
        """Retire the editor head while retaining versions bound to instances."""
        card = self.services.world.get_card(card_id)
        default = self._default_document(card)
        if default and default["definition"].get("status_entity_id"):
            saved = self.save(card_id, default["definition"], default["presentation"])
            self.activate(card_id, saved["definition_version"])
            return
        with self.database.transaction(immediate=True) as db:
            db.execute("UPDATE state_machine_instances SET enabled=0 WHERE card_id=?", (card_id,))
            db.execute("""INSERT INTO state_machine_editors(card_id,definition_version,revision,presentation_json)
                       VALUES(?,0,1,'{}') ON CONFLICT(card_id) DO UPDATE SET
                       definition_version=0,revision=revision+1,presentation_json='{}'""", (card_id,))

    def import_legacy(self, card_id, definition):
        if definition is None:
            self.clear(card_id)
            return
        value, names, aliases = self._merge_system_groups(card_id, definition, disabled=True)
        default = self._default_document(self.services.world.get_card(card_id))
        presentation = deepcopy((default or {}).get("presentation", {"positions": {}}))
        for group in definition["entities"]:
            positions = {state["id"]: state["position"] for state in group["states"] if "position" in state}
            if positions:
                presentation["positions"][names.get(group["id"], group["id"])] = positions
        self.save(card_id, value, consolidated_presentation(presentation, aliases, (default or {}).get("presentation", {})), legacy=True)

    def migrate_legacy(self):
        """Import existing preview configs once, always with no enabled instance."""
        with self.database.transaction(immediate=True) as db:
            rows = db.execute("SELECT id,config_json FROM cards").fetchall()
            for row in rows:
                config = json.loads(row["config_json"])
                if "state_machine" not in config:
                    continue
                machine = config.pop("state_machine")
                if machine is not None and not self.get(row["id"])["definition_version"]:
                    self.import_legacy(row["id"], machine)
                db.execute("UPDATE cards SET config_json=? WHERE id=?", (json.dumps(config), row["id"]))

    def capture(self, card_id, card_ids):
        saved = self.get(card_id)
        if saved["definition"] is None:
            return None
        value = remap_definition(saved["definition"], card_ids)
        system_ids = [group["id"] for group in value["entities"] if group.get("ownership") == "system"]
        value["entities"] = [group for group in value["entities"] if group["id"] not in system_ids]
        return {"definition": value, "system_groups": system_ids, "presentation": saved["presentation"],
                "enabled": saved["enabled"]}

    def restore(self, card_id, captured, card_ids):
        # Every deployed object receives its own definition; foreign references
        # are remapped once, never copied into a Legion's definition.
        value = remap_definition(captured["definition"], card_ids)
        if "system_groups" in captured:
            declared = self._default_document(self.services.world.get_card(card_id))
            groups = [group for group in (declared or {}).get("definition", {}).get("entities", []) if group.get("ownership") == "system"]
            if not set(captured["system_groups"]) <= {group["id"] for group in groups}:
                raise ResourceValidationError("Template incompatibility: target type no longer provides a referenced system group")
            value["entities"] = groups + value["entities"]
        value, _, aliases = self._merge_system_groups(card_id, value,
            disabled="system_groups" not in captured and not captured.get("enabled", False))
        if aliases:
            captured = {**captured, "presentation": consolidated_presentation(captured.get("presentation", {}), aliases,
                (self._default_document(self.services.world.get_card(card_id)) or {}).get("presentation", {}))}
        try:
            return self.save(card_id, value, captured.get("presentation"), legacy="system_groups" not in captured)
        except (ValueError, ResourceValidationError) as exc:
            raise ResourceValidationError(f"Template incompatibility: {exc}") from exc

    def members(self, card_id):
        self.services.world.get_card(card_id)
        members = self.services.world.list_members(card_id)
        return [{"id": member.id, "name": member.name, "type": member.type,
                 "has_definition": self.has_definition(member), "state_machine_editor": self.has_editor(member),
                 "has_members": self.services.world.is_container(member)}
                for member in members]

    def _scope(self, instance_id):
        return self.services.state.ensure_scope("node_document", "state-machine-" + instance_id, schema_id="core.node_document")

    def _cursor(self):
        with self.database.locked() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='operation_events'").fetchone()
            return int(db.execute("SELECT COALESCE(MAX(sequence),0) FROM operation_events").fetchone()[0]) if exists else 0

    def _ensure_instance(self, card_id, version, scope_key, context, cursor):
        with self.database.transaction(immediate=True) as db:
            row = db.execute("SELECT id,definition_version FROM state_machine_instances WHERE card_id=? AND scope_key=?", (card_id, scope_key)).fetchone()
            if row:
                if int(row["definition_version"]) != version:
                    raise ConflictError("This scope is bound to another definition version; choose a new scope for the edited definition")
                return self.get_instance(row["id"])
            definition = self.get_definition(card_id, version)
            instance_id = str(uuid4())
            db.execute("INSERT INTO state_machine_instances(id,card_id,definition_version,scope_key,enabled) VALUES(?,?,?,?,0)",
                       (instance_id, card_id, version, scope_key))
            self.services.state.set(self._scope(instance_id), "data", {
                "context": context or {}, "states": {entity.id: entity.initial_state for entity in definition.entities},
                "bindings": {}, "memory": {}, "cursor": cursor})
            return self.get_instance(instance_id)

    def _prepare_instance(self, card_id, version, definition, scope_key="default", context=None, cursor=None):
        bound = self.node_instance(card_id)
        if bound and definition.status_entity_id and bound[0]["definition_version"] != version:
            old = bound[0]
            states = {entity.id: old["states"].get(entity.id, entity.initial_state)
                      if old["states"].get(entity.id) in {state.id for state in entity.states} else entity.initial_state
                      for entity in definition.entities}
            with self.database.transaction(immediate=True) as db:
                db.execute("UPDATE state_machine_instances SET definition_version=? WHERE id=?", (version, old["id"]))
                db.execute("UPDATE state_machine_actions SET status='cancelled',error='Definition replaced before dispatch' WHERE instance_id=? AND status IN ('pending','waiting_capacity')", (old["id"],))
            self.put_runtime(old["id"], states=states, memory={}, bindings={}, cursor=self._cursor())
        return self._ensure_instance(card_id, version, scope_key, context, self._cursor() if cursor is None else cursor)

    def activate_restored(self, card_ids):
        """Bind all restored owners before resolving references, including cycles."""
        with self.database.transaction(immediate=True):
            for card_id in card_ids:
                saved = self.get(card_id)
                definition = self.get_definition(card_id, saved["definition_version"])
                self.validate_system_groups(card_id, definition)
                self._prepare_instance(card_id, saved["definition_version"], definition)
            for card_id in card_ids:
                self.activate(card_id, notify=False)

    def activate(self, card_id, definition_version=None, scope_key="default", context=None, cursor=None, *, notify=True, _initializing=False):
        version = definition_version or self.get(card_id)["definition_version"]
        if not version:
            raise ResourceValidationError("Save a state-machine definition before enabling it")
        definition = self.get_definition(card_id, version)
        self.validate_system_groups(card_id, definition)
        if not _initializing:
            self.validate_dependents(card_id, definition)
        if definition.status_entity_id and (scope_key != "default" or context):
            raise ResourceValidationError("Node status has one instance; session scopes are not supported")
        from backend.capabilities.events import CALL_EVENTS, WORK_EVENTS, ACTION_EVENTS, RUN_EVENTS, STATE_EVENTS
        from backend.agent_state_machine import AGENT_ACTIVITY_EVENTS
        phases = {event.key for events in (CALL_EVENTS, WORK_EVENTS, ACTION_EVENTS, RUN_EVENTS, STATE_EVENTS) for event in events.values()} | {"custom", "state.exited", "state.current"} | AGENT_ACTIVITY_EVENTS.keys()
        for rule in definition.rules:
            if not rule.enabled:
                continue
            triggers = [rule.trigger] + ([signal.match for signal in rule.program.signals] if rule.program else [])
            if any(trigger.event not in phases for trigger in triggers):
                raise ResourceValidationError("Choose a supported trigger phase before enabling a transition")
            if any(action.kind == "run" and (not isinstance(action.arguments.get("prompt"), str) or not action.arguments["prompt"].strip()) for action in rule.actions):
                raise ResourceValidationError("Run actions require a prompt before enabling a transition")
        with self.database.transaction(immediate=True):
            instance = self._prepare_instance(card_id, version, definition, scope_key, context, cursor)
            bindings = dict(instance.get("bindings", {}))
            for ref in definition.references:
                if ref.entity_id in bindings:
                    continue
                bound_target = self.node_instance(ref.card_id)
                existing = bound_target[0] if bound_target else self._bound_instance(ref.card_id, scope_key)
                target_version = existing["definition_version"] if existing else self.get(ref.card_id)["definition_version"]
                if not target_version:
                    raise ResourceValidationError("Save referenced state-machine definitions before enabling them")
                target_definition = self.get_definition(ref.card_id, target_version)
                target_group = ref.state_group_id or target_definition.entities[0].id
                if target_group not in {entity.id for entity in target_definition.entities}:
                    raise ResourceValidationError("Referenced state group does not exist in the bound definition")
                if bound_target and target_definition.status_entity_id:
                    target = bound_target[0]
                else:
                    target = self._ensure_instance(ref.card_id, target_version, scope_key, context, self._cursor() if cursor is None else cursor)
                bindings[ref.entity_id] = {"instance_id": target["id"], "entity_id": target_group}
            targets = [(item["instance_id"], item["entity_id"]) for item in bindings.values()]
            local_targets = {(instance["id"], entity.id) for entity in definition.entities}
            if len(set(targets)) != len(targets) or set(targets) & local_targets:
                raise ResourceValidationError("A state group may be referenced only once in a definition")
            instance = self.put_runtime(instance["id"], bindings=bindings)
            try:
                resolved = self.get_evaluator_definition(instance["id"])
                self.validate_commands(resolved)
            except ValidationError as exc:
                raise ResourceValidationError(f"Referenced states do not match the bound definition: {exc}") from exc
            result = self.put_runtime(instance["id"], enabled=True)
            if notify:
                self.publish_status(instance["id"])
            return result

    def validate_commands(self, machine):
        from backend.state_machine_preview import rule_actions
        for rule in machine.rules:
            if not rule.enabled or not rule.command:
                continue
            group = next(group for group in machine.entities if group.id == rule.command.entity_id)
            node = self.services.world.get_card(group.card_id)
            action = rule_actions(machine, rule)[0]
            if action.kind == "run":
                if action.operation_id != "host:run" or not self.services.plugins.has_trait(node.type, "core.agent"):
                    raise ResourceValidationError("Command must reference the existing RunManager entry point on an Agent")
                if not isinstance(action.arguments.get("prompt"), str) or not action.arguments["prompt"].strip():
                    raise ResourceValidationError("Run commands require a prompt before enabling a transition")
            elif action.kind == "node_action":
                self.services.state_machine_runtime._node_action(node.id, action.model_dump())
            else:
                self.services.plugins.capability_definition(action.capability)
                if action.operation_id != f"capability:{action.capability}":
                    raise ResourceValidationError("Command operation identity does not match its registered capability")

    def publish_status(self, instance_id):
        from backend.events.models import EventType, RuntimeEvent
        instance = self.get_instance(instance_id)
        if self.node_status(instance["card_id"]) is None:
            return
        card = self.services.enrich_card(self.services.world.get_card(instance["card_id"]))
        self.services.events.publish_event_nowait(RuntimeEvent(type=EventType.CARD_UPDATED, node_id=card.id,
                                           payload={"node": card.model_dump(mode="json")}))

    def get_instance(self, instance_id):
        with self.database.locked() as db:
            row = db.execute("SELECT * FROM state_machine_instances WHERE id=?", (instance_id,)).fetchone()
        if not row:
            raise NotFoundError("State-machine instance does not exist")
        record = self.services.state.get_record(self._scope(instance_id), "data")
        return {**dict(row), **record.value, "enabled": bool(row["enabled"]), "revision": record.revision}

    def list_instances(self, enabled_only=True):
        with self.database.locked() as db:
            rows = db.execute("SELECT id FROM state_machine_instances" + (" WHERE enabled=1" if enabled_only else "") + " ORDER BY id").fetchall()
        return [self.get_instance(row[0]) for row in rows]

    def put_runtime(self, instance_id, *, states=None, memory=None, cursor=None, enabled=None, expected_revision=None, bindings=None, _projection=False):
        with self.database.transaction(immediate=True) as db:
            instance = self.get_instance(instance_id)
            if states is not None and not _projection:
                definition = self.get_definition(instance["card_id"], instance["definition_version"])
                for group in definition.entities:
                    if group.ownership == "system" and states.get(group.id) != instance["states"].get(group.id):
                        raise ResourceValidationError("System state cannot be directly assigned")
            scope = self._scope(instance_id)
            value = self.services.state.get(scope, "data")
            for key, update in (("states", states), ("memory", memory), ("cursor", cursor), ("bindings", bindings)):
                if update is not None:
                    value[key] = update
            self.services.state.set(scope, "data", value, expected_revision=expected_revision)
            if enabled is not None:
                db.execute("UPDATE state_machine_instances SET enabled=? WHERE id=?", (int(enabled), instance_id))
        return self.get_instance(instance_id)

    def resolve_binding(self, instance_id, entity_id):
        binding = self.get_instance(instance_id).get("bindings", {}).get(entity_id)
        return (binding["instance_id"], binding["entity_id"]) if binding else (instance_id, entity_id)

    def get_states(self, instance_id):
        instance = self.get_instance(instance_id)
        states = dict(instance["states"])
        for alias, binding in instance.get("bindings", {}).items():
            states[alias] = self.get_instance(binding["instance_id"])["states"][binding["entity_id"]]
        return states

    def put_states(self, instance_id, states):
        with self.database.transaction(immediate=True):
            updates = {}
            targets = {}
            for entity_id, state in states.items():
                owner_id, group_id = self.resolve_binding(instance_id, entity_id)
                target = (owner_id, group_id)
                if target in targets and targets[target] != state:
                    raise ResourceValidationError("Cannot assign conflicting states to the same state group")
                targets[target] = state
                owner = self.get_instance(owner_id)
                definition = self.get_definition(owner["card_id"], owner["definition_version"])
                group = next((entity for entity in definition.entities if entity.id == group_id), None)
                if not group or state not in {item.id for item in group.states}:
                    raise ResourceValidationError("Cannot assign an unknown user-defined state")
                if group.ownership == "system":
                    raise ResourceValidationError("System state cannot be directly assigned; invoke a legal command")
                updates.setdefault(owner_id, dict(owner["states"]))[group_id] = state
            changed = []
            for owner_id, values in updates.items():
                if values != self.get_instance(owner_id)["states"]:
                    self.put_runtime(owner_id, states=values)
                    changed.append(owner_id)
            for owner_id in changed:
                self.publish_status(owner_id)

    def get_evaluator_definition(self, instance_id):
        instance = self.get_instance(instance_id)
        definition = self.get_definition(instance["card_id"], instance["definition_version"])
        self.validate_system_groups(instance["card_id"], definition)
        value = definition.model_dump(mode="json", exclude_none=True)
        for reference in value.pop("references", []):
            binding = instance["bindings"][reference["entity_id"]]
            target = self.get_instance(binding["instance_id"])
            definition = self.get_definition(target["card_id"], target["definition_version"])
            group = next(entity for entity in definition.entities if entity.id == binding["entity_id"])
            entity = group.model_dump(mode="json", exclude_none=True)
            entity.update(id=reference["entity_id"], card_id=target["card_id"])
            entity.pop("parent_id", None)
            value["entities"].append(entity)
        return StateMachineConfig.model_validate(value)

    def resolve_preview_definition(self, machine, *, replacements=None):
        """Resolve pointers without activating, binding, or writing runtime state."""
        definition = StateMachineConfig.model_validate(machine)
        value = definition.model_dump(mode="json", exclude_none=True)
        for reference in value.pop("references", []):
            target = (replacements or {}).get(reference["card_id"]) or self._reference_definition(reference["card_id"])
            group_id = reference.get("state_group_id") or target.entities[0].id
            group = next((entity for entity in target.entities if entity.id == group_id), None)
            if group is None:
                raise ResourceValidationError("Referenced state group does not exist in the preview definition")
            entity = group.model_dump(mode="json", exclude_none=True)
            entity.update(id=reference["entity_id"], card_id=reference["card_id"])
            entity.pop("parent_id", None)
            value["entities"].append(entity)
        try:
            return StateMachineConfig.model_validate(value)
        except ValidationError as exc:
            raise ResourceValidationError(f"Referenced states do not match the preview definition: {exc}") from exc
