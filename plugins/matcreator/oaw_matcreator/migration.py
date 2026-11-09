"""Pack-owned import using OAW host stores; never replay operations.

Loaded by the explicit migration CLI in the OAW host, never by its isolated
research worker.
"""
from __future__ import annotations

import base64
from contextlib import ExitStack, closing
from dataclasses import replace
from datetime import datetime, timezone
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from backend.config import Settings
from backend.errors import ConflictError, ResourceValidationError
from backend.node_documents import read_document, write_document
from backend.storage_location import LOCK, file_lock


def identity(*parts):
    return str(uuid5(NAMESPACE_URL, json.dumps(parts, ensure_ascii=False)))


def remap_text(text, source, destination):
    separator = "\\" if "\\" in str(destination) else "/"
    for old in dict.fromkeys((source, source.replace("\\", "/"), source.replace("/", "\\"))):
        pattern = re.escape(old.rstrip("/\\")) + r"(?=[/\\\s\"'`)]|$)(?P<tail>(?:[/\\][^\s\"'`<>]*)?)"
        text = re.sub(pattern, lambda match: str(destination) + match["tail"].replace("\\", separator).replace("/", separator), text)
    return text


def _relative(raw):
    from open_agent_world.skill_packages import Skill
    Skill(files={raw: ""})  # Host portable resource-path validation.
    return raw.split("/")


def _linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)


def _resource_files(root, relative, warnings):
    folder = root if relative == "." else root.joinpath(*_relative(relative))
    if not folder.resolve().is_relative_to(root) or not folder.is_dir():
        raise ResourceValidationError(f"Skill directory is unavailable: {relative}")
    files = {}
    for base, dirs, names in os.walk(folder, followlinks=False):
        base = Path(base)
        dirs[:] = [name for name in dirs if name not in {".git", ".venv", "__pycache__", "node_modules"}
            and not _linked(base / name)]
        for name in names:
            path = base / name
            if name == "SKILL.md" and base == folder:
                continue
            if _linked(path):
                warnings.append(f"Skill link retained only in original workspace: {path.relative_to(root)}")
                continue
            if name.startswith(".env") or name.endswith(".pyc") or name.casefold().endswith(":zone.identifier"):
                continue
            if path.stat().st_size > 1024 * 1024:
                warnings.append(f"Large skill resource remains in Sandbox: {path.relative_to(root)}")
                continue
            key = path.relative_to(folder).as_posix()
            _relative(key)
            data = path.read_bytes()
            try:
                files[key] = data.decode("utf-8")
            except UnicodeDecodeError:
                files[key] = {"data_base64": base64.b64encode(data).decode(),
                    "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}
    instructions = folder / "SKILL.md"
    if _linked(instructions) or not instructions.resolve().is_relative_to(root):
        raise ResourceValidationError("Skill instructions must stay inside the selected workspace")
    return instructions.read_text(encoding="utf-8"), files


def knowledge_document(manifest, workspace, external_skills=None):
    """Translate supported semantics and retain original metadata as provenance."""
    from open_agent_world.skill_packages import Skill
    entries, edges, skills, warnings = [], [], [], []
    source_id = str(UUID(manifest["source_id"]))
    keys, known = {}, set()
    for item in manifest["knowledge"]["entries"]:
        raw = item["raw"]
        metadata = raw.get("metadata") or {}
        custom = metadata.get("custom") or {}
        if metadata.get("disabled") or custom.get("matcreator_disabled") or metadata.get("verification_status") in {"bugged", "deprecated"}:
            warnings.append(f"Inactive knowledge retained in source attachment: {raw.get('title', item['key'])}")
            continue
        key = identity(source_id, "entry", item["key"])
        if key in known:
            raise ResourceValidationError("Duplicate source knowledge identity")
        known.add(key)
        keys[item["key"]] = key
        kind = raw.get("entry_type", "capability")
        kind = {"constraint": "heuristic", "analytical": "heuristic", "environment": "heuristic",
            "tool": "procedure", "repository": "procedure", "data": "procedure"}.get(kind, kind)
        if kind not in {"capability", "procedure", "heuristic", "memory"}:
            kind = "capability"
        entry = {"id": key, "title": str(raw.get("title") or raw.get("name") or "Imported knowledge")[:200],
            "type": kind, "content": raw.get("content") or raw.get("description") or "",
            "tags": raw.get("tags") or [], "aliases": raw.get("aliases") or [],
            "owner": "user", "refinement": {"refined": "refined", "validated": "published"}.get(metadata.get("refinement_status"), "pending"),
            "verification": {"peer_reviewed": "reviewed", "community_tested": "tested"}.get(metadata.get("verification_status"), "unverified"),
            "trust": metadata.get("trust_score") if metadata.get("trust_score") is not None else 0.5,
            "usage_count": metadata.get("usage_count") or 0,
            "success": raw.get("success", custom.get("success")),
            "created_at": manifest["created_at"],
            "provenance": {"migration_source": source_id, "source_key": item["key"], "database": item["database"], "legacy": raw}}
        if raw.get("created_at"):
            parsed = datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00"))
            entry["created_at"] = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()
        attachments = []
        for asset in json.loads(raw.get("assets_json") or "[]"):
            attachments.append((asset["folder"] + "/" + asset["filename"], asset.get("content", "")))
        for script in json.loads(raw.get("scripts_json") or "[]"):
            attachments.append(("scripts/" + script["filename"], script.get("content", "")))
        if attachments:
            skill_id = identity(source_id, "entry-skill", item["key"])
            files = dict(attachments)
            for path in files:
                _relative(path)
            skills.append(Skill(id=skill_id, name=entry["title"][:120], instructions=entry["content"], files=files).model_dump(mode="json"))
            entry["resources"] = [{"skill_id": skill_id, "path": "SKILL.md"}]
        entries.append(entry)
    relations = {"depends_on": "dependency", "belongs_to": "refinement_of", "relates_to": "related_workflow",
        "requires": "prerequisite", "discovered_in": "derived_from", "similar_to": "related_workflow", "supersedes": "replacement"}
    by_id = {item["id"]: item for item in entries}
    seen = set()
    for item in manifest["knowledge"]["edges"]:
        source, target = keys.get(item["source"]), keys.get(item["target"])
        relation = relations.get(item["relation"], item["relation"])
        valid = relation in {"dependency", "prerequisite", "refinement_of", "related_workflow", "derived_from",
            "heuristic_for", "related_memory", "replacement"} and source and target and source != target
        if valid:
            kinds = {by_id[source]["type"], by_id[target]["type"]}
            valid = relation not in {"dependency", "prerequisite", "related_workflow"} or kinds <= {"capability", "procedure"}
            valid &= relation != "replacement" or len(kinds) == 1
            valid &= relation != "related_memory" or "memory" in kinds
            valid &= relation != "heuristic_for" or (by_id[source]["type"] == "heuristic" and by_id[target]["type"] in {"capability", "procedure"})
        if not valid:
            warnings.append(f"Unmapped relationship retained in source attachment: {item['relation']}")
            continue
        triple = (source, target, relation)
        if triple not in seen:
            seen.add(triple)
            edges.append({"id": identity(source_id, *triple), "source": source, "target": target, "relation": relation})
    for item in manifest["skills"]:
        external = (external_skills or {}).get(item["id"])
        skill_root = Path(external["root"]).resolve(strict=True) if external else workspace
        relative = Path(external["path"]).relative_to(skill_root).as_posix() if external else item["path"]
        instructions, files = _resource_files(skill_root, relative, warnings)
        key = identity(source_id, "skill", item["id"])
        instructions = ("Imported standalone MatCreator skill. Use connected OAW tools and the selected Sandbox. "
            f"Original resources, including large files, are in {'the read-only Imported skills Sandbox' if external else 'the project Sandbox'}, directory {relative}.\n\n" + instructions)
        skills.append(Skill(id=key, name=item["name"], instructions=instructions, files=files).model_dump(mode="json"))
        entries.append({"id": key, "title": item["name"], "type": "capability", "content": instructions,
            "owner": "user", "created_at": manifest["created_at"], "resources": [{"skill_id": key, "path": "SKILL.md"}],
            "provenance": {"migration_source": source_id, "source_key": item["id"], "workspace_path": item["path"]}})
    for entry in entries:
        for alias in manifest.get("workspace_aliases", [manifest["workspace"]]):
            entry["content"] = remap_text(entry["content"], alias, workspace)
    for skill in skills:
        for alias in manifest.get("workspace_aliases", [manifest["workspace"]]):
            skill["instructions"] = remap_text(skill["instructions"], alias, workspace)
    return {"entries": entries, "edges": edges, "skills": skills, "snapshots": {}}, warnings


def _group_nodes(services, group_id, known_ids=None):
    group = services.world.get_card(group_id)
    if group.type != "legion" or group.config.get("mode") != "group":
        raise ResourceValidationError("--group-id must select a MatCreator research group")
    result = dict(known_ids or {"group": group_id})
    for key, type_id in {"agent": "matcreator.agent", "conversation": "conversation", "sandbox": "sandbox", "knowledge": "matcreator.kdg"}.items():
        matches = [node.id for node in services.world.descendants(group_id) if node.type == type_id
            and (known_ids is None or node.id == known_ids[key])]
        if len(matches) != 1:
            raise ResourceValidationError(f"Target group must contain exactly one {type_id}")
        result[key] = matches[0]
    return result


def _remaining_import_cards(db, receipt):
    ids = {*receipt.get("node_ids", {}).values(), *receipt.get("external_sandboxes", {}).values()}
    return {node_id for node_id in ids if db.execute("SELECT 1 FROM cards WHERE id=?", (node_id,)).fetchone()}


def _order_history(db, session_id, message_ids):
    """Backfill source replies in order, keeping destination-only messages after history."""
    current = [row[0] for row in db.execute(
        "SELECT id FROM conversation_messages WHERE session_id=? ORDER BY sequence", (session_id,))]
    imported = set(message_ids)
    ordered = message_ids + [message_id for message_id in current if message_id not in imported]
    if ordered == current:
        return
    # Vacate the session's unique sequence values before assigning the new order.
    offset = db.execute("SELECT MAX(sequence) FROM conversation_messages WHERE session_id=?", (session_id,)).fetchone()[0]
    db.execute("UPDATE conversation_messages SET sequence=sequence+? WHERE session_id=?", (offset, session_id))
    db.executemany("UPDATE conversation_messages SET sequence=? WHERE id=? AND session_id=?",
        ((index, message_id, session_id) for index, message_id in enumerate(ordered, 1)))


async def _attach(services, conversation_id, session_id, filename, value, content, data=None):
    from backend.conversations.models import ConversationAttachment
    async def chunks():
        if data is None:
            yield json.dumps(value, ensure_ascii=False).encode("utf-8")
        else:
            for chunk in data:
                yield chunk
    archive = await services.resources.artifacts.upload(services, conversation_id, session_id, filename, chunks())
    try:
        services.conversations.add_message(conversation_id, session_id, sender_kind="system", sender_id=None,
            sender_name="MatCreator import", content=content, attachments=[ConversationAttachment(
                version_id=archive["version_id"], path=filename, name=filename, size_bytes=archive["size_bytes"], media_type="application/json")])
    except BaseException:
        await services.resources.artifacts.remove_bytes(archive)
        raise
    return archive


async def import_into(services, manifest, workspace, *, group_id=None, runtime="auto", read_only=False, external_skills=None, history_reader=None, progress=None, copy_history=False):
    """One transaction for records under the host's graph mutation lock."""
    from backend.conversations.models import ConversationSessionCreate
    from backend.legions.models import LegionInstantiate
    from backend.legions.presets import preset_record
    from backend.world.models import CardPatch, CardCreate, EdgeCreate
    from backend.card_library import LibraryEdit
    if history_reader is None:
        from .migrate import history_archives
        history_reader = history_archives
    source_id = str(UUID(manifest["source_id"]))
    workspace = Path(workspace).resolve(strict=True)
    incoming, warnings = knowledge_document(manifest, workspace, external_skills)
    spec = services.plugins.node_type("matcreator.kdg").document
    incoming = spec.model.model_validate(incoming).model_dump(mode="json")
    receipt_key = "migration.matcreator." + source_id
    uploaded = []
    totals = {"sessions": len(manifest["sessions"]), "messages": sum(len(s["messages"]) for s in manifest["sessions"]),
        "knowledge_entries": len(manifest["knowledge"]["entries"]), "skills": len(manifest["skills"])}
    description = (f"Migration complete: {totals['sessions']} sessions, {totals['messages']} messages, "
        f"{totals['knowledge_entries']} knowledge entries, {totals['skills']} Skills.")
    report = {"source_id": source_id, "sessions_added": 0, "messages_added": 0,
        "knowledge_added": 0, "skills_added": 0, "history_parts_added": 0, "history_references_added": 0, "conflicts": [], "warnings": warnings}
    async with services._node_mutation():
        try:
            with services.events.committed_batch(), services.database.transaction(immediate=True) as db:
                saved = db.execute("SELECT value_json FROM application_settings WHERE key=?", (receipt_key,)).fetchone()
                receipt = json.loads(saved[0]) if saved else {"sessions": {}}
                previous = receipt.get("node_ids")
                deleted_group = None
                if previous and not db.execute("SELECT 1 FROM cards WHERE id=?", (previous["group"],)).fetchone():
                    if _remaining_import_cards(db, receipt):
                        raise ConflictError("The previous imported Legion was removed, but some of its cards still exist. "
                            "Restore that Legion or delete its remaining imported cards before importing again.")
                    deleted_group = previous["group"]
                    receipt, previous = {"sessions": {}}, None
                if previous and group_id and group_id != previous["group"]:
                    raise ConflictError("This source was already imported into another group in this profile")
                if previous or group_id:
                    ids = _group_nodes(services, previous["group"] if previous else group_id, previous)
                    if services.run_manager and services.run_manager.store.current(agent_id=ids["agent"]):
                        raise ConflictError("Stop this imported research Agent before updating its history")
                else:
                    record = preset_record("matcreator.native-research", services.plugins)
                    for node in record.blueprint.nodes:
                        if node.key == "sandbox":
                            node.config.update(workspace_path=str(workspace), workspace_scope="shared", runtime=runtime,
                                workspace_access="read_only" if read_only else "read_write")
                        elif node.key == "group":
                            node.name = "MatCreator imported research"
                            node.config["description"] = description
                    existing = services.world.list_cards()
                    position = {"x": max((node.position.x + node.size.width for node in existing), default=0) + 320,
                        "y": min((node.position.y for node in existing), default=0) + 320}
                    instance = await services.instantiate_legion(record.id, LegionInstantiate(position=position), record=record)
                    ids = instance.node_ids
                group = services.world.get_card(ids["group"])
                native = next(preset for preset in services.plugins.legion_presets() if preset.id == "matcreator.native-research")
                default_description = next(node.config.get("description") for node in native.nodes if node.key == "group")
                if group.config.get("description") in {default_description, receipt.get("group_description")}:
                    await services.update_card(group.id, CardPatch(config={**group.config, "description": description}))
                receipt["group_description"] = description
                sandbox = services.world.get_card(ids["sandbox"])
                existing_path = sandbox.config.get("workspace_path")
                if existing_path and Path(existing_path).resolve() != workspace:
                    raise ConflictError("Target Sandbox already uses a different workspace; choose another group")
                if existing_path is None:
                    used = db.execute("SELECT 1 FROM runs WHERE agent_id=? LIMIT 1", (ids["agent"],)).fetchone()
                    if used:
                        raise ConflictError("Choose a fresh group; this group's Agent already has execution history")
                    await services.update_card(ids["sandbox"], CardPatch(config={**sandbox.config,
                        "workspace_path": str(workspace), "workspace_scope": "shared", "runtime": runtime,
                        "workspace_access": "read_only" if read_only else "read_write"}))
                for folder in dict.fromkeys(item["root"] for item in (external_skills or {}).values()):
                    attached = receipt.setdefault("external_sandboxes", {})
                    if folder not in attached:
                        node = await services.create_card(CardCreate(type="sandbox", parent_id=ids["group"],
                            name="Imported skills (read only)", position={"x": 1902, "y": 1700},
                            config={"workspace_path": folder, "workspace_scope": "shared", "workspace_access": "read_only", "runtime": runtime}))
                        await services.create_edge(EdgeCreate(source=ids["agent"], target=node.id, relationship="execute"))
                        attached[folder] = node.id
                current = read_document(services, ids["knowledge"])
                combined = current["value"]
                changed = False
                for field in ("entries", "skills", "edges"):
                    existing = {item["id"]: item for item in combined[field]}
                    for item in incoming[field]:
                        if item["id"] not in existing:
                            combined[field].append(item)
                            existing[item["id"]] = item
                            changed = True
                            if field == "entries":
                                report["knowledge_added"] += 1
                            elif field == "skills":
                                report["skills_added"] += 1
                        elif field != "edges":
                            old = {k: v for k, v in existing[item["id"]].items() if k not in {"node_id", "created_at"}}
                            new = {k: v for k, v in item.items() if k not in {"node_id", "created_at"}}
                            if old != new:
                                report["conflicts"].append({"kind": field, "id": item["id"]})
                if changed:
                    write_document(services, ids["knowledge"], combined, current["revision"])
                for source in manifest["sessions"]:
                    session_id = receipt["sessions"].get(source["key"])
                    created = session_id is None
                    if session_id:
                        services.conversations.get_session(ids["conversation"], session_id)
                    else:
                        session = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(
                            title=source["title"], participant_ids=[ids["agent"]]))
                        session_id = session.id
                        receipt["sessions"][source["key"]] = session_id
                        report["sessions_added"] += 1
                    added = False
                    message_ids = []
                    for message in source["messages"]:
                        message_id = identity(source_id, source["key"], message["id"])
                        message_ids.append(message_id)
                        existing = db.execute("SELECT content FROM conversation_messages WHERE id=?", (message_id,)).fetchone()
                        text = message["content"]
                        for alias in manifest.get("workspace_aliases", [manifest["workspace"]]):
                            text = remap_text(text, alias, workspace)
                        if existing:
                            if existing[0] != text.strip():
                                report["conflicts"].append({"kind": "message", "id": message_id})
                            continue
                        if message["sender_kind"] not in {"user", "agent"}:
                            raise ResourceValidationError("Unsupported migrated message role")
                        services.conversations.add_message(ids["conversation"], session_id,
                            sender_kind=message["sender_kind"], sender_id=ids["agent"] if message["sender_kind"] == "agent" else None,
                            sender_name="MatCreator" if message["sender_kind"] == "agent" else "You",
                            content=text, message_id=message_id)
                        db.execute("UPDATE conversation_messages SET created_at=? WHERE id=?", (message["created_at"], message_id))
                        report["messages_added"] += 1
                        added = True
                    if added:
                        _order_history(db, session_id, message_ids)
                    if added or created:
                        if copy_history:
                            for name, chunks in history_reader(source):
                                if progress:
                                    progress(f"Saving history {source['id']}: {name}")
                                uploaded.append(await _attach(services, ids["conversation"], session_id, name, None,
                                    "Imported history. Original tool events, state and execution graph are attached as read-only records. "
                                    f"Files are in the connected Sandbox. Original workspace: {manifest['workspace']}; current workspace: {workspace}. "
                                    "No old tasks were restarted.", data=chunks))
                                report["history_parts_added"] += 1
                        else:
                            origin = source["legacy"]
                            reference = {"format": "oaw.matcreator-history-reference", "version": 1,
                                "database": origin["database"], "source_session": origin["session"],
                                "event_count_at_import": origin["event_count"], "agent_graph": origin["agent_graph_path"],
                                "native_source": manifest.get("native_source"), "access": "read_only"}
                            uploaded.append(await _attach(services, ids["conversation"], session_id, "matcreator-history-reference.json", reference,
                                "Imported conversation history. Complete tool events and execution records stay in their original database; "
                                "the attached index records their location. Project files stay in the connected Sandbox. No old tasks were restarted."))
                            report["history_references_added"] += 1
                    if created:
                        db.execute("UPDATE conversation_sessions SET created_at=?, updated_at=? WHERE id=?",
                            (source["created_at"], source["updated_at"], session_id))
                if not receipt.get("knowledge_archive") or report["knowledge_added"] or any(c["kind"] != "message" for c in report["conflicts"]):
                    if not receipt.get("knowledge_archive"):
                        receipt["knowledge_archive"] = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(
                            title="MatCreator migration sources", participant_ids=[ids["agent"]])).id
                    uploaded.append(await _attach(services, ids["conversation"], receipt["knowledge_archive"], "matcreator-knowledge.json", manifest["knowledge"],
                        "Original knowledge records, including unsupported relationships and inactive entries."))
                receipt.update(node_ids=ids, workspace=str(workspace), files=manifest["files"], path_mapping={manifest["workspace"]: str(workspace)})
                db.execute("INSERT OR REPLACE INTO application_settings VALUES (?,?)", (receipt_key, json.dumps(receipt)))
                report["node_ids"] = ids
                library = services.card_library
                state = library.read()
                if deleted_group:
                    for old_deck in state.decks:
                        entries = [entry for entry in old_deck.entries
                            if not (entry.kind == "legion_instance" and entry.id == deleted_group)]
                        if entries != old_deck.entries:
                            state = library.edit(LibraryEdit(action="update_deck", id=old_deck.id,
                                entries=entries, expected_revision=state.revision))
                deck = next((deck for deck in state.decks if deck.id == "saved-legions"),
                    next((deck for deck in state.decks if deck.name == "Legions"), None))
                if deck is None:
                    state = library.edit(LibraryEdit(action="create_deck", name="Legions", icon="layers", expected_revision=state.revision))
                    deck = next(deck for deck in state.decks if deck.id == state.active_deck_id)
                library.edit(LibraryEdit(action="move_entry", id=deck.id,
                    entry={"kind": "legion_instance", "id": ids["group"]}, expected_revision=state.revision))
                report.update(status="complete", totals=totals,
                    recreated_deleted_import=deleted_group is not None,
                    location={"deck_id": deck.id, "deck_name": deck.name, "group_id": ids["group"],
                        "group_name": services.world.get_card(ids["group"]).name})
                from backend.execution_config import configuration_summary
                configuration = configuration_summary(services, ids["sandbox"])
                issues = [{"name": item["name"], "source": item["source"],
                    "status": item.get("status", "unconfigured"),
                    "message": item.get("error") or "Configure this variable on the destination OAW host."}
                    for item in configuration["variables"] if not item["configured"] or item.get("available") is False]
                report["destination_settings"] = {"policy": "preserve_oaw", "environment_ready": configuration["ready"],
                    "environment_variables": [item["name"] for item in configuration["variables"]], "issues": issues}
                if issues:
                    report["warnings"].append("Data import succeeded; existing destination OAW settings were preserved. "
                        "Execution setup requires attention: " + ", ".join(item["name"] for item in issues)
                        + ". Rebind these variables in OAW; source credentials were not imported.")
        except BaseException:
            for archive in uploaded:
                await services.resources.artifacts.remove_bytes(archive)
            raise
    return report


def validate_import(manifest, data_root, workspace, *, external_skills=None, copy_history=False, extracting=False):
    from .migrate import disk_directory, ensure_space
    from backend.sandbox.manager import SandboxManager
    from backend.sandbox.registry import SandboxRuntimeRegistry
    root = Path(data_root).expanduser().resolve(strict=True)
    if (root / "deployment.json").exists():
        raise ResourceValidationError("Use a local OAW profile, not a hosted/deployment data directory")
    database = root / "database" / "world.sqlite3"
    if not database.is_file():
        raise ResourceValidationError("--data-root must select an initialized OAW profile with MatCreator installed and enabled")
    workspace = Path(workspace).expanduser().absolute()
    validator = SandboxManager(root, SandboxRuntimeRegistry())
    validator.validate_environment_path(str(workspace), allow_missing=extracting)
    for item in (external_skills or {}).values():
        validator.validate_workspace(item["root"])
    metadata_bytes = len(json.dumps(manifest, ensure_ascii=False).encode("utf-8")) * 2 + 8 * 1024 * 1024
    history_bytes = sum(item["size"] for source in manifest["sessions"] for item in source["legacy"].get("archives", [])) if copy_history else 0
    wal = Path(str(database) + "-wal")
    backup_bytes = database.stat().st_size + (wal.stat().st_size if wal.exists() else 0) if copy_history else 0
    budgets = {root.stat().st_dev: [root, metadata_bytes + history_bytes + backup_bytes]}
    if extracting:
        directory = disk_directory(workspace.parent)
        budget = budgets.setdefault(directory.stat().st_dev, [directory, 0])
        budget[1] += sum(item["size"] for item in manifest["files"])
    for directory, required in budgets.values():
        ensure_space(directory, required)
    return root, workspace, validator


async def _import_profile(services, manifest, *, workspace, group_id=None, runtime="auto", read_only=False,
                      extract=None, external_skills=None, history_reader=None, progress=None, copy_history=False):
    """Import into opened stores while the CLI owns the profile file lock."""
    import asyncio
    root, workspace, validator = validate_import(manifest, services.settings.data_root, workspace,
        external_skills=external_skills, copy_history=copy_history, extracting=extract is not None)
    database = root / "database" / "world.sqlite3"
    async with services._node_mutation():
        if extract:
            with services.database.locked() as db:
                saved = db.execute("SELECT value_json FROM application_settings WHERE key=?",
                    ("migration.matcreator." + str(UUID(manifest["source_id"])),)).fetchone()
            previous = json.loads(saved[0]) if saved else None
            if previous and previous.get("files") != manifest["files"]:
                raise ConflictError("Package file selection changed since this source was imported; choose a separate destination profile")
            if not (previous and Path(previous["workspace"]) == workspace and workspace.is_dir()):
                await asyncio.to_thread(extract)
        validator.validate_workspace(str(workspace))
        backup = None
        if copy_history:
            backup_dir = root / "backups" / "matcreator"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".sqlite3")
            def save_backup():
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(backup)) as target:
                    source.backup(target)
            await asyncio.to_thread(save_backup)
        result = await import_into(services, manifest, workspace, group_id=group_id, runtime=runtime, read_only=read_only,
            external_skills=external_skills, history_reader=history_reader, progress=progress, copy_history=copy_history)
        result["database_backup"] = str(backup) if backup else None
        return result


async def import_migration(manifest, data_root, *, workspace, group_id=None, runtime="auto", read_only=False, extract=None, external_skills=None, history_reader=None, progress=None, copy_history=False):
    from backend.services import create_services
    root, workspace, _ = validate_import(manifest, data_root, workspace,
        external_skills=external_skills, copy_history=copy_history, extracting=extract is not None)
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(root / LOCK))
        except ConflictError as error:
            raise ConflictError("The destination OAW profile is in use. Stop its backend before importing "
                "(Ctrl+C in the terminal running scripts/dev.ps1). Closing the browser does not stop the backend. "
                "Then rerun the migration command.") from error
        services = create_services(replace(Settings.for_data_root(root), agent_runtime=None, sandbox_runtime=None))
        try:
            return await _import_profile(services, manifest, workspace=workspace, group_id=group_id, runtime=runtime,
                read_only=read_only, extract=extract, external_skills=external_skills,
                history_reader=history_reader, progress=progress, copy_history=copy_history)
        finally:
            services.close()
